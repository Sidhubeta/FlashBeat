import asyncio, os, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from flashbeat.core.resolver import Resolver, ResolveError, _ttl_from_url
from flashbeat.core.models import Track


class Fake(Resolver):
    def __init__(self, inner=None, pyyt=None, dlp=None, sc=None):
        super().__init__()
        self.calls = []
        self._inner, self._pyyt, self._dlp, self._sc = inner, pyyt, dlp, sc

    async def _search_innertube(self, q, n):
        self.calls.append("inner"); return await self._inner(q, n)

    async def _search_pyyt(self, q, n):
        self.calls.append("pyyt"); return await self._pyyt(q, n)

    async def _search_ytdlp(self, prefix, q, n):
        self.calls.append(prefix); fn = self._dlp if prefix == "ytsearch" else self._sc
        return await fn(q, n)


def T(i): return Track(id=i, title="t" + i, duration=100)


def test_race_first_wins_and_cache():
    async def run():
        async def slow(q, n): await asyncio.sleep(0.3); return [T("slow")]
        async def fast(q, n): await asyncio.sleep(0.02); return [T("fast")]
        async def never(q, n): raise AssertionError("hedge must not start")
        r = Fake(slow, fast, never)
        a = await r.search("Hello  World", 1)
        assert a[0].id == "fast" and "ytsearch" not in r.calls
        a[0].title = "mutated"                                 # callers must get copies
        b = await r.search("hello world", 1, video=True)
        assert b[0].title == "tfast" and b[0].video is True and r.calls.count("inner") == 1
    asyncio.run(run())


def test_failures_fall_to_hedge_then_soundcloud():
    async def run():
        async def boom(q, n): raise RuntimeError("blocked")
        async def empty(q, n): return []
        async def dlp(q, n): return [T("dlp")]
        r = Fake(boom, empty, dlp)
        t0 = time.perf_counter()
        res = await r.search("x", 1)
        assert res[0].id == "dlp" and time.perf_counter() - t0 < 1.0   # both primaries done -> hedge instantly

        async def dlp_fail(q, n): raise ResolveError("nope")
        async def sc(q, n): return [Track(id="9", title="sc", source="soundcloud")]
        r2 = Fake(boom, empty, dlp_fail, sc)
        assert (await r2.search("y", 1))[0].source == "soundcloud"

        r3 = Fake(boom, empty, dlp_fail, dlp_fail)
        assert await r3.search("z", 1) == []
    asyncio.run(run())


def test_hedge_after_timeout():
    async def run():
        async def hang(q, n): await asyncio.sleep(5); return [T("hang")]
        async def dlp(q, n): return [T("dlp")]
        r = Fake(hang, hang, dlp)
        t0 = time.perf_counter()
        res = await r.search("q", 1)
        el = time.perf_counter() - t0
        assert res[0].id == "dlp" and 1.4 < el < 2.2, el          # hedge fires at ~1.5 s
    asyncio.run(run())


def test_resolve_cache_and_singleflight():
    async def run():
        r = Resolver()
        n = {"c": 0}

        async def impl(track):
            n["c"] += 1; await asyncio.sleep(0.05)
            url = "https://rr1.googlevideo.com/x?expire=%d" % (time.time() + 7200)
            r._cache_stream(track, url, {"User-Agent": "ua"}); return url, {"User-Agent": "ua"}
        r._resolve_impl = impl
        tracks = [T("same") for _ in range(4)]
        await asyncio.gather(*(r.resolve(t) for t in tracks))
        assert n["c"] == 1 and all(t.stream_url for t in tracks) and tracks[0].headers == {"User-Agent": "ua"}
        t5 = T("same"); await r.resolve(t5); assert n["c"] == 1 and t5.stream_url      # cache hit
        t6 = T("same"); await r.resolve(t6, fresh=True); assert n["c"] == 2             # fresh bypasses cache
        d = Track(id="d", title="radio", source="direct", stream_url="http://x/y.m3u8")
        await r.resolve(d); assert n["c"] == 2
        assert 7000 < _ttl_from_url("https://a/b?expire=%d" % (time.time() + 7500)) < 7300
        assert _ttl_from_url("https://a/b") == 3600
    asyncio.run(run())


def test_download_cached_file(tmp_dir="/tmp/fbtest_dl"):
    import flashbeat.core.resolver as R
    os.makedirs(tmp_dir, exist_ok=True)
    R.DOWNLOAD_DIR = tmp_dir
    for f in os.listdir(tmp_dir): os.remove(os.path.join(tmp_dir, f))
    open(os.path.join(tmp_dir, "abc.webm"), "wb").write(b"1")
    open(os.path.join(tmp_dir, "abc.video.mp4"), "wb").write(b"1")
    open(os.path.join(tmp_dir, "part1.webm.part"), "wb").write(b"1")
    r = Resolver()
    assert r.cached_file(Track(id="abc", title="a")).endswith("abc.webm")
    assert r.cached_file(Track(id="abc", title="a", video=True)).endswith("abc.video.mp4")
    assert r.cached_file(Track(id="part1", title="a")) is None


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn(); print("ok", name)
