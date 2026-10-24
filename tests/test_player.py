import asyncio, os, sys, tempfile, time
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from flashbeat.config import config
from flashbeat.core.errors import NoVoiceChat, StreamFailed
from flashbeat.core.models import Track
from flashbeat.core.queue import QueueManager, LOOP_QUEUE
from flashbeat.core.resolver import ResolveError
from flashbeat.core.store import Store, JsonBackend
from flashbeat.core.player import Player

config.IDLE_LEAVE_SECONDS = 0


class FakeEngine:
    def __init__(self):
        self.played, self.fail_stream, self.no_vc = [], 0, False
        self.on_stream_end = self.on_chat_closed = None
        self.left = []

    async def play(self, chat_id, track, seek=0):
        if self.no_vc: raise NoVoiceChat()
        use_file = bool(track.file_path and os.path.exists(track.file_path))
        if not use_file and self.fail_stream > 0:
            self.fail_stream -= 1; raise StreamFailed("403")
        track.via = "file" if use_file else "stream"
        self.played.append((track.id, track.via, seek))

    async def pause(self, c): pass
    async def resume(self, c): pass
    async def leave(self, c): self.left.append(c)
    async def set_volume(self, c, v): return True


class FakeResolver:
    def __init__(self): self.bad, self.downloads, self.fresh, self.prefetched, self.protect = set(), [], [], [], set()
    async def resolve(self, t, fresh=False):
        if t.id in self.bad: raise ResolveError("blocked")
        if fresh: self.fresh.append(t.id)
        t.stream_url = f"http://s/{t.id}"; return t
    async def download(self, t):
        self.downloads.append(t.id)
        if t.id in self.bad: return None
        p = f"/tmp/fb_{t.id}.webm"; open(p, "wb").write(b"x"); t.file_path = p; return p
    def prefetch(self, t):
        if t: self.prefetched.append(t.id)
    def warm_download(self, t): pass
    async def related(self, seed, exclude, limit=1):
        return [Track(id=f"rel{len(exclude)}", title="rel", duration=100)]


class FakeAnnouncer:
    def __init__(self): self.log = []
    async def now_playing(self, chat_id, track, status=None): self.log.append(("np", track.id))
    async def finished(self, chat_id, track): self.log.append(("fin", track.id))
    async def failed(self, chat_id, track, status=None): self.log.append(("fail", track.id))
    async def notice(self, chat_id, kind): self.log.append(("notice", kind))
    async def refresh(self, chat_id): self.log.append(("refresh",))


def T(i, dur=200): return Track(id=i, title="t" + i, duration=dur)


async def rig(tmp):
    store = Store(JsonBackend(os.path.join(tmp, "s.json"))); await store.load()
    eng, res, ann = FakeEngine(), FakeResolver(), FakeAnnouncer()
    p = Player(eng, QueueManager(), store, res, ann)
    return p, eng, res, ann, store


def test_basic_flow():
    async def run():
        with tempfile.TemporaryDirectory() as tmp:
            p, eng, res, ann, store = await rig(tmp)
            C = -100
            assert p.enqueue(C, T("a")) == 0 and p.enqueue(C, T("b")) == 1 and p.enqueue(C, T("c"), "next") == 1
            assert await p.start(C) is True and p.is_active(C)
            await asyncio.sleep(0)
            assert eng.played == [("a", "stream", 0)] and res.prefetched[-1] == "c"
            assert store.recent(C)[0]["id"] == "a"
            await eng.on_stream_end(C)                      # a ends normally? (elapsed < dur) -> would heal; fake it
            # a was "premature" (elapsed ~0 of 200s) so first end heals at same position
            assert eng.played[-1][0] == "a" and res.fresh == ["a"]
            assert (await p.pause(C)) and p.is_paused(C) and not await p.pause(C)
            assert (await p.resume(C)) and not p.is_paused(C)
            nxt = await p.skip(C)
            assert nxt.id == "c" and eng.played[-1][0] == "c"
            await p.stop(C)
            assert not p.is_active(C) and eng.left == [C] and p.queue.peek(C) is None
    asyncio.run(run())


def test_normal_end_advances_and_finishes():
    async def run():
        with tempfile.TemporaryDirectory() as tmp:
            p, eng, res, ann, store = await rig(tmp)
            C = -1
            p.enqueue(C, T("a", dur=10)); p.enqueue(C, T("b", dur=10))      # short -> never "premature"
            await p.start(C)
            await eng.on_stream_end(C)
            assert eng.played[-1][0] == "b"
            await eng.on_stream_end(C)                                      # queue ran dry, idle_leave=0 -> teardown
            assert not p.is_active(C) and eng.left == [C]
            assert ("fin", "a") in ann.log and ("fin", "b") in ann.log
    asyncio.run(run())


def test_fallback_to_file_and_skip_unplayable():
    async def run():
        with tempfile.TemporaryDirectory() as tmp:
            p, eng, res, ann, store = await rig(tmp)
            C = -2
            eng.fail_stream = 1                       # stream rejected once -> download fallback
            p.enqueue(C, T("a", 10)); await p.start(C)
            assert res.downloads == ["a"] and eng.played[-1] == ("a", "file", 0)

            res.bad = {"x"}                           # unresolvable AND undownloadable -> skipped
            p.enqueue(C, T("x", 10)); p.enqueue(C, T("y", 10))
            await eng.on_stream_end(C)
            assert ("fail", "x") in ann.log and eng.played[-1][0] == "y"
    asyncio.run(run())


def test_self_heal_resumes_at_position():
    async def run():
        with tempfile.TemporaryDirectory() as tmp:
            p, eng, res, ann, store = await rig(tmp)
            C = -3
            p.enqueue(C, T("a", 300)); p.enqueue(C, T("b", 300)); await p.start(C)
            t = p.current(C)
            t.started_at -= 100                       # pretend 100 s were played
            await eng.on_stream_end(C)
            assert eng.played[-1] == ("a", "stream", 100) and t.retries == 1 and res.fresh == ["a"]
            t.started_at -= 20
            await eng.on_stream_end(C)                # retry 2 -> download + play from file
            assert eng.played[-1][1] == "file" and eng.played[-1][2] >= 120 and t.retries == 2
            t.file_path = None; t.via = "stream"; t.started_at -= 5
            await eng.on_stream_end(C)                # out of retries -> moves on
            assert p.current(C).id == "b"
    asyncio.run(run())


def test_no_voice_chat_and_seek_and_loop():
    async def run():
        with tempfile.TemporaryDirectory() as tmp:
            p, eng, res, ann, store = await rig(tmp)
            C = -4
            eng.no_vc = True
            p.enqueue(C, T("a")); assert await p.start(C) is False
            assert ("notice", "no_vc") in ann.log and not p.is_active(C)
            eng.no_vc = False
            p.enqueue(C, T("a", 100)); await p.start(C)
            assert await p.seek(C, to=40) == 40 and eng.played[-1] == ("a", "stream", 40)
            assert 40 <= p.current(C).elapsed() <= 41
            assert await p.seek(C, delta=30) in range(70, 72)
            assert await p.seek(C, to=99) == -1
            await p.set_loop(C, LOOP_QUEUE)
            p.enqueue(C, T("b", 10))
            await p.skip(C)
            assert [t.id for t in p.queue.get(C).all()] == ["b", "a"]
    asyncio.run(run())


def test_autoplay():
    async def run():
        with tempfile.TemporaryDirectory() as tmp:
            p, eng, res, ann, store = await rig(tmp)
            C = -5
            store.set_chat(C, autoplay=True)
            p.enqueue(C, T("a", 10)); await p.start(C)
            await asyncio.sleep(0.05)                 # prefill task runs
            assert [t.id for t in p.queue.get(C).upcoming][0].startswith("rel")
            await eng.on_stream_end(C)
            assert eng.played[-1][0].startswith("rel") and p.current(C).requested_by == "Autoplay"
            assert await p.toggle_autoplay(C) is False
    asyncio.run(run())


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn(); print("ok", name)
