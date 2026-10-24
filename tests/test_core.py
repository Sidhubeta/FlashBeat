import asyncio, os, sys, tempfile, time
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from flashbeat.helpers import text as T
from flashbeat.core.cache import TTLCache, SingleFlight, prune_dir
from flashbeat.core.models import Track
from flashbeat.core.queue import ChatQueue, LOOP_TRACK, LOOP_QUEUE
from flashbeat.core.store import Store, JsonBackend
from flashbeat.core import innertube


def tr(i, **kw):
    return Track(id=i, title=f"t{i}", duration=200, **kw)


def test_text():
    assert T.fmt_time(65) == "1:05" and T.fmt_time(3725) == "1:02:05"
    assert T.parse_time("3:45") == 225 and T.parse_time("1:02:03") == 3723 and T.parse_time("x") == 0
    assert T.youtube_id("https://youtu.be/dQw4w9WgXcQ?t=3") == "dQw4w9WgXcQ"
    assert T.youtube_id("https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=RDdQw4w9WgXcQ") == "dQw4w9WgXcQ"
    assert T.youtube_id("https://music.youtube.com/watch?v=dQw4w9WgXcQ") == "dQw4w9WgXcQ"
    assert T.youtube_id("https://www.youtube.com/shorts/dQw4w9WgXcQ") == "dQw4w9WgXcQ"
    assert T.youtube_id("https://example.com/watch?v=dQw4w9WgXcQ") is None
    assert T.classify_url("https://www.youtube.com/playlist?list=PLabc123") == "yt_playlist"
    assert T.classify_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=RDx") == "yt_video"
    assert T.classify_url("https://soundcloud.com/a/b") == "other"
    assert T.find_url("play this https://youtu.be/dQw4w9WgXcQ, please") == "https://youtu.be/dQw4w9WgXcQ"
    assert T.parse_flags(["-f", "-v", "hello", "-f"]) == ({"f", "v"}, "hello -f")
    assert T.human_count(1234567) == "1.2M" and T.human_count(999) == "999"
    assert len(T.progress_bar(50, 100, 14)) == 14 and T.progress_bar(0, 0, 5) == "━━━━━"
    assert T.short("a" * 50, 10).endswith("…") and len(T.short("a" * 50, 10)) == 10


def test_cache():
    now = [0.0]
    c = TTLCache(2, ttl=10, clock=lambda: now[0])
    c.set("a", 1); c.set("b", 2); c.set("c", 3)
    assert c.get("a") is None and c.get("b") == 2
    now[0] = 11
    assert c.get("b") is None and "c" not in c
    c.set("x", 1, ttl=100)
    now[0] = 50
    assert c.get("x") == 1


def test_singleflight():
    async def run():
        sf, calls = SingleFlight(), []

        async def job():
            calls.append(1)
            await asyncio.sleep(0.05)
            return 42

        res = await asyncio.gather(*(sf.do("k", job) for _ in range(5)))
        assert res == [42] * 5 and len(calls) == 1
        assert not sf.pending("k")
    asyncio.run(run())


def test_prune():
    with tempfile.TemporaryDirectory() as d:
        for i in range(5):
            p = os.path.join(d, f"f{i}")
            open(p, "wb").write(b"x" * 100)
            os.utime(p, (1000 + i, 1000 + i))
        freed = prune_dir(d, 250)
        assert freed == 300 and sorted(os.listdir(d)) == ["f3", "f4"]


def test_track_timing():
    t = tr("a")
    t.mark_started(10)
    assert 10 <= t.elapsed() <= 11
    t.pause(); e = t.elapsed(); time.sleep(0.05); assert t.elapsed() == e
    t.resume(); assert t.elapsed() >= e
    assert tr("b").fresh_copy().started_at == 0.0


def test_queue():
    q = ChatQueue()
    assert q.add(tr("1")) == 0 and q.add(tr("2")) == 1 and q.add(tr("3")) == 2
    assert q.add_next(tr("9")) == 1 and [t.id for t in q.upcoming] == ["9", "2", "3"]
    assert q.advance().id == "9" and q.history[-1] == "1"
    q.loop = LOOP_TRACK
    assert q.advance().id == "9"            # repeats
    assert q.advance(skip=True).id == "2"   # skip ignores track-loop
    q.loop = LOOP_QUEUE
    assert q.advance().id == "3" and [t.id for t in q.upcoming] == ["2"]
    assert q.remove(1).id == "2" and q.remove(5) is None
    q.add(tr("a")); q.add(tr("b")); q.add(tr("c"))
    assert q.move(3, 1).id == "c" and [t.id for t in q.upcoming][0] == "c"
    assert q.shuffle() == 3 and q.clear_upcoming() == 3
    q.loop = 0
    assert q.advance() is None and q.current is None and q.last.id == "3"
    q.force(tr("z")); assert q.current.id == "z"


def test_store_json():
    async def run():
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "s.json")
            s = Store(JsonBackend(path), flush_interval=0.05)
            await s.load()
            assert s.chat(-100)["volume"] == 100 and s.setting(-100, "autoplay") is False
            s.set_chat(-100, autoplay=True)
            s.add_auth(-100, 5); s.add_auth(-100, 5)
            s.ban(77); s.add_sudo(9)
            assert s.toggle_fav(1, {"id": "x", "title": "X"}) is True
            assert s.toggle_fav(1, {"id": "x", "title": "X"}) is False
            for i in range(3):
                s.record_play(-100, {"id": "v1", "title": "One"})
            s.record_play(-100, {"id": "v2", "title": "Two"})
            assert s.top(-100)[0]["id"] == "v1" and s.top(-100)[0]["n"] == 3 and s.recent(-100)[0]["id"] == "v2"
            assert s.register_chat(-100) is False and s.register_user(5) is True
            await asyncio.sleep(0.2)          # background flusher writes
            await s.close()
            s2 = Store(JsonBackend(path)); await s2.load()
            assert s2.setting(-100, "autoplay") is True and s2.is_auth(-100, 5)
            assert 77 in s2.blacklist and 9 in s2.sudoers and s2.total_plays() == 4
            assert s2.auth_users(-100) == {5} and s2.favs(1) == []
            await s2.close()
    asyncio.run(run())


def test_innertube():
    fixture = {"contents": {"twoColumnSearchResultsRenderer": {"primaryContents": {"sectionListRenderer": {"contents": [
        {"itemSectionRenderer": {"contents": [
            {"adSlotRenderer": {}},
            {"videoRenderer": {"videoId": "aaaaaaaaaaa", "title": {"runs": [{"text": "Song "}, {"text": "A"}]},
                               "lengthText": {"simpleText": "3:45"}, "ownerText": {"runs": [{"text": "Chan"}]},
                               "viewCountText": {"simpleText": "1,234,567 views"}}},
            {"videoRenderer": {"videoId": "liveliveliv", "title": {"runs": [{"text": "LIVE"}]}}},
            {"videoRenderer": {"videoId": "bbbbbbbbbbb", "title": {"simpleText": "Song B"},
                               "lengthText": {"simpleText": "1:02:03"}, "longBylineText": {"runs": [{"text": "C2"}]}}},
        ]}}]}}}}}
    out = innertube.parse_search(fixture, 5)
    assert [o["id"] for o in out] == ["aaaaaaaaaaa", "bbbbbbbbbbb"]
    assert out[0]["title"] == "Song A" and out[0]["duration"] == 225 and out[0]["views"] == "1.2M"
    assert out[1]["duration"] == 3723 and out[1]["channel"] == "C2"
    assert len(innertube.parse_search(fixture, 1)) == 1


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn(); print("ok", name)
