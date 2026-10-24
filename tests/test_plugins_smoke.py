import asyncio, importlib, os, sys, tempfile, types
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
sys.path.insert(0, os.path.dirname(__file__))
import stubs; stubs.install()
os.environ.update(API_ID="1", API_HASH="x", BOT_TOKEN="x", OWNER_ID="7", LOGGER_ID="-1")

tmp = tempfile.mkdtemp(); os.chdir(tmp)
from flashbeat.config import config
config.DATA_DIR = tmp; config.IDLE_LEAVE_SECONDS = 0; config.THUMB_GEN = False


class Msg:
    def __init__(self, text, chat_id=-100, uid=5):
        self.text, self.caption, self.entities, self.caption_entities = text, None, None, None
        self.reply_to_message = None; self.id = 1
        self.chat = types.SimpleNamespace(id=chat_id, username="grp", type=types.SimpleNamespace(name="SUPERGROUP"))
        self.from_user = types.SimpleNamespace(id=uid, mention="<a>U</a>")
        self.sender_chat = None; self.replies = []; self.command = text.lstrip("/").split()
    async def reply_text(self, text, **kw):
        self.replies.append(text); s = Msg(text); s.edits = []; s.reply_markup = kw.get("reply_markup")
        async def edit_text(t, **k): s.edits.append(t); s.reply_markup = k.get("reply_markup"); return s
        s.edit_text = edit_text; self.status = s; return s
    async def delete(self): pass


async def main():
    from flashbeat import runtime as rt
    from flashbeat.plugins import MODULES
    for n in MODULES: importlib.import_module(f"flashbeat.plugins.{n}")
    await rt.store.load()
    from flashbeat.core.models import Track
    from flashbeat.core.errors import StreamFailed

    played = []
    async def fake_play(chat_id, track, seek=0): track.via = "stream"; played.append((track.id, seek))
    async def noop(*a, **k): return None
    rt.engine.play, rt.engine.pause, rt.engine.resume, rt.engine.leave = fake_play, noop, noop, noop
    async def fake_search(q, n=1, video=False): return [Track(id=f"id{i}", title=f"{q} #{i}", duration=200, url="https://y/x", channel="Ch") for i in range(n)]
    async def fake_resolve(t, fresh=False): t.stream_url = "http://s"; return t
    rt.resolver.search, rt.resolver.resolve = fake_search, fake_resolve
    rt.resolver.prefetch = lambda t: None; rt.resolver.warm_download = lambda t: None
    rt.assistants.clients = [types.SimpleNamespace(me=types.SimpleNamespace(id=9, mention="A"), fb_index=0)]
    rt.joined.set(-100, True)
    async def send_message(*a, **k): return types.SimpleNamespace(id=2)
    rt.bot.send_message = send_message

    from flashbeat.plugins import play, controls, queue_cmds, library
    m = Msg("/play lofi beats"); m.command = ["play", "lofi", "beats"]
    await play.play_cmd(None, m)
    assert played == [("id0", 0)], played
    assert rt.player.current(-100).title == "lofi beats #0" and rt.player.is_active(-100)

    m2 = Msg("/play second song"); m2.command = ["play"]
    await play.play_cmd(None, m2)
    assert "Queued #1" in m2.status.edits[-1], m2.status.edits

    m3 = Msg("/queue"); await queue_cmds.queue_cmd(None, m3)
    assert "second song" in m3.replies[-1]

    m4 = Msg("/play -n -f third"); await play.play_cmd(None, m4)        # force needs privilege -> uid 5 is not admin
    assert "Only admins, authorised" in m4.status.edits[-1]

    m5 = Msg("/skip"); await controls.skip_cmd(None, m5)                 # requester (uid 5) may skip own song
    assert "Up next" in m5.replies[-1] and played[-1][0] == "id0"
    m6 = Msg("/loop track"); await controls.loop_cmd(None, m6); assert "track" in m6.replies[-1]
    m7 = Msg("/seek 30"); await controls.seek_cmd(None, m7); assert "0:30" in m7.replies[-1], m7.replies
    m8 = Msg("/fav"); await library.fav_cmd(None, m8); assert "Saved" in m8.replies[-1]
    m9 = Msg("/stop", uid=99); await controls.stop_cmd(None, m9); assert "Only admins" in m9.replies[-1]
    m10 = Msg("/stop"); await controls.stop_cmd(None, m10); assert not rt.player.is_active(-100)
    m11 = Msg("/play"); m11.command = ["play"]; await play.play_cmd(None, m11); assert "Play something" in m11.replies[-1]
    print("smoke ok; handlers registered:", len(sys.modules["pyrogram"].Client.handlers))

asyncio.run(main())
