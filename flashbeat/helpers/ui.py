"""All user-facing text and keyboards."""
from __future__ import annotations

from pyrogram.types import InlineKeyboardButton as B
from pyrogram.types import InlineKeyboardMarkup as M

from flashbeat.config import config
from flashbeat.core.models import Track
from flashbeat.core.queue import LOOP_NAMES, LOOP_OFF, LOOP_QUEUE, LOOP_TRACK, ChatQueue
from flashbeat.helpers.text import esc, fmt_time, short

NOTHING_PLAYING = "🔇 Nothing is playing right now."
NOT_ALLOWED = "🚫 Only admins, authorised users or the person who requested the song can do that."
USAGE_PLAY = ("🎧 <b>Play something</b>\n\n<code>/play song name</code>\n<code>/play youtube/soundcloud link</code>\n"
              "or reply to an audio/video file with /play\n\n"
              "Flags: <code>-f</code> play now · <code>-n</code> play next · <code>-v</code> video")
ADMIN_REQUIRED = "⚠️ Give me the <b>Invite users via link</b> admin permission so my assistant can join."
ANON_ADMIN = "👤 Anonymous admin mode isn't supported for /play - please send it from your own account."
QUEUE_FULL = "📦 The queue is full ({0} tracks)."
NOTICES = {
    "no_vc": "📵 <b>No active voice chat.</b>\nStart one in this group and try again.",
    "tg_down": "⚠️ Telegram's voice servers hiccuped (or RTMP is active). I cleared the queue and left - try again.",
    "idle_left": "👋 Nobody queued anything, so I left the voice chat. Use /play to bring me back.",
}


def searching(query: str = "") -> str:
    return f"⚡ <b>Searching</b> <i>{esc(short(query, 60))}</i> …" if query else "⚡ <b>Working on it</b> …"


def not_found() -> str:
    tail = f"\nNeed help? {config.SUPPORT_CHAT}" if config.SUPPORT_CHAT else ""
    return "😕 I couldn't find or open that. Try different words or another link." + tail


def too_long(minutes: int) -> str:
    return f"⏳ That's longer than the {minutes}-minute limit."


def _meta(t: Track) -> str:
    bits = [f"⏱ {t.duration_text}"]
    if t.views:
        bits.append(f"👁 {esc(t.views)}")
    return "  ·  ".join(bits)


def _title(t: Track) -> str:
    title = esc(short(t.title, 70))
    return f'<a href="{esc(t.url)}">{title}</a>' if t.url.startswith("http") else f"<b>{title}</b>"


def now_playing(t: Track, *, paused: bool = False, loop: int = LOOP_OFF, autoplay: bool = False) -> str:
    head = "⏸ <b>Paused</b>" if paused else "⚡ <b>Now Playing</b>"
    lines = [head, "", f"🎵 {_title(t)}"]
    if t.channel:
        lines.append(f"👤 {esc(short(t.channel, 40))}")
    lines.append(_meta(t))
    if t.requested_by:
        lines.append(f"🙋 {t.requested_by}")
    flags = []
    if loop == LOOP_TRACK:
        flags.append("🔂 loop track")
    elif loop == LOOP_QUEUE:
        flags.append("🔁 loop queue")
    if autoplay:
        flags.append("🤖 autoplay")
    if t.video:
        flags.append("🎬 video")
    if flags:
        lines += ["", " · ".join(flags)]
    return "\n".join(lines)


def progress(t: Track) -> str:
    return f"<code>{t.bar(16)}</code>\n{fmt_time(t.elapsed())} / {t.duration_text}"


def queued(t: Track, pos: int, wait: int) -> str:
    eta = f"\n⏳ Starts in ~{fmt_time(wait)}" if wait else ""
    return f"➕ <b>Queued #{pos}</b>\n\n🎵 {_title(t)}\n{_meta(t)}\n🙋 {t.requested_by}{eta}"


def failed(t: Track) -> str:
    return f"⚠️ Couldn't play <b>{esc(short(t.title, 50))}</b> - skipping."


def playlist_added(n: int) -> str:
    return f"📚 Added <b>{n}</b> more track{'s' if n != 1 else ''} from the playlist."


def controls(paused: bool, loop: int, autoplay: bool) -> M:
    loop_label = {LOOP_OFF: "➡️ Loop", LOOP_TRACK: "🔂 Track", LOOP_QUEUE: "🔁 Queue"}[loop]
    return M([
        [B("▶️ Resume" if paused else "⏸ Pause", callback_data="fb|pause"),
         B("⏭ Skip", callback_data="fb|skip"), B("⏹ Stop", callback_data="fb|stop")],
        [B(loop_label, callback_data="fb|loop"), B("🔀 Shuffle", callback_data="fb|shuffle"),
         B("❤️ Save", callback_data="fb|fav")],
        [B("📃 Queue", callback_data="fb|queue|0"), B("🎤 Lyrics", callback_data="fb|lyrics"),
         B("🤖 Auto ✓" if autoplay else "🤖 Auto", callback_data="fb|auto")],
    ])


def queued_kb(track_id: str) -> M:
    return M([[B("▶️ Play now", callback_data=f"fb|playnow|{track_id}"),
               B("🗑 Remove", callback_data=f"fb|rm|{track_id}")]])


def search_results(query: str, tracks: list[Track]) -> str:
    lines = [f"🔎 <b>Results for</b> <i>{esc(short(query, 50))}</i>\n"]
    for i, t in enumerate(tracks, 1):
        lines.append(f"<b>{i}.</b> {esc(short(t.title, 55))}\n    <i>{esc(short(t.channel, 30))}</i> · {t.duration_text}")
    return "\n".join(lines)


def search_kb(token: str, n: int) -> M:
    nums = [B(str(i + 1), callback_data=f"fb|pick|{token}|{i}") for i in range(n)]
    rows = [nums[i:i + 4] for i in range(0, n, 4)]
    rows.append([B("✖️ Close", callback_data="fb|close")])
    return M(rows)


PAGE = 8


def queue_text(q: ChatQueue, page: int) -> tuple[str, int]:
    items = list(q.upcoming)
    pages = max(1, -(-len(items) // PAGE))
    page = min(max(page, 0), pages - 1)
    total = sum(t.duration for t in q.all())
    lines = [f"📃 <b>Queue</b> · {len(q)} track{'s' if len(q) != 1 else ''}" + (f" · {fmt_time(total)}" if total else "")]
    if q.current:
        lines += ["", f"▶️ <b>Now:</b> {esc(short(q.current.title, 55))} <i>({q.current.duration_text})</i>"]
    if items:
        lines.append("")
    for i, t in enumerate(items[page * PAGE:(page + 1) * PAGE], page * PAGE + 1):
        lines.append(f"<b>{i}.</b> {esc(short(t.title, 50))} · {t.duration_text}")
    if not items:
        lines.append("\n<i>Nothing queued after this song.</i>")
    if pages > 1:
        lines.append(f"\n<i>Page {page + 1}/{pages}</i>")
    return "\n".join(lines), page


def queue_kb(page: int, pages_total: int) -> M:
    row = []
    if page > 0:
        row.append(B("◀️", callback_data=f"fb|qp|{page - 1}"))
    row.append(B("✖️", callback_data="fb|close"))
    if page < pages_total - 1:
        row.append(B("▶️", callback_data=f"fb|qp|{page + 1}"))
    return M([row])


def pages_total(q: ChatQueue) -> int:
    return max(1, -(-len(q.upcoming) // PAGE))


def lyrics_kb(token: str, page: int, total: int) -> M:
    row = []
    if page > 0:
        row.append(B("◀️", callback_data=f"fb|lyr|{token}|{page - 1}"))
    row.append(B("✖️", callback_data="fb|close"))
    if page < total - 1:
        row.append(B("▶️", callback_data=f"fb|lyr|{token}|{page + 1}"))
    return M([row])


def favs_text(items: list[dict]) -> str:
    if not items:
        return "💔 No saved songs yet. Tap ❤️ Save on a now-playing card or use /fav."
    lines = ["❤️ <b>Your saved songs</b>\n"]
    lines += [f"<b>{i}.</b> {esc(short(it['title'], 55))}" for i, it in enumerate(items[:20], 1)]
    return "\n".join(lines)


def favs_kb(n: int) -> M:
    n = min(n, 20)
    nums = [B(f"▶ {i + 1}", callback_data=f"fb|favplay|{i}") for i in range(n)]
    rows = [nums[i:i + 5] for i in range(0, n, 5)]
    rows.append([B("▶ Play all", callback_data="fb|favplay|all"), B("✖️ Close", callback_data="fb|close")])
    return M(rows)


def settings_text(s: dict) -> str:
    on = lambda v: "✅ on" if v else "❌ off"  # noqa: E731
    return ("⚙️ <b>Chat settings</b>\n\n"
            f"👮 Admin-only /play: {on(s['admin_only'])}\n"
            f"🧹 Delete command messages: {on(s['delete_cmds'])}\n"
            f"🤖 Autoplay related songs: {on(s['autoplay'])}\n"
            f"🔊 Volume: {s['volume']}%")


def settings_kb(s: dict) -> M:
    return M([
        [B("👮 Admin-only play", callback_data="fb|set|admin_only")],
        [B("🧹 Delete commands", callback_data="fb|set|delete_cmds")],
        [B("🤖 Autoplay", callback_data="fb|set|autoplay")],
        [B("✖️ Close", callback_data="fb|close")],
    ])


HELP = {
    "play": ("🎧 <b>Playing</b>\n\n/play <i>name | link | reply to file</i>\n/vplay - play as video\n/playnext - queue to play next\n"
             "/playforce - play right now\n/search <i>name</i> - pick from 6 results\n"
             "Flags: <code>-f</code> force · <code>-n</code> next · <code>-v</code> video"),
    "ctrl": ("🎛 <b>Controls</b>\n\n/pause · /resume · /skip · /stop\n/seek <i>10</i> · /seekback <i>10</i> · /seekto <i>1:30</i>\n"
             "/loop <i>off|track|queue</i> · /volume <i>1-200</i>\n/shuffle · /remove <i>n</i> · /move <i>a b</i> · /clear\n"
             "/queue · /np"),
    "more": ("✨ <b>Extras</b>\n\n/lyrics <i>[song]</i> - lyrics\n/fav - save current song · /favs · /playfav <i>[n|all]</i>\n"
             "/history · /top - this chat's most played\n/autoplay <i>on|off</i> - never run out of music\n/settings - chat options"),
    "admin": ("👮 <b>Admin</b>\n\n/auth · /unauth · /authlist - let members control playback\n/reload - refresh admin list\n"
              "/settings - admin-only play, cleanup, autoplay"),
    "sudo": ("🛠 <b>Owner / sudo</b>\n\n/stats · /ping · /active\n/broadcast <i>[-user] [-nochat]</i> (reply)\n"
             "/ban · /unban <i>id</i> · /addsudo · /rmsudo · /sudolist\n/logs · /clearcache · /restart"),
}


def help_kb(current: str = "") -> M:
    names = [("🎧 Play", "play"), ("🎛 Controls", "ctrl"), ("✨ Extras", "more"), ("👮 Admin", "admin"), ("🛠 Sudo", "sudo")]
    rows = [[B(("• " if k == current else "") + n, callback_data=f"fb|help|{k}") for n, k in names[:3]],
            [B(("• " if k == current else "") + n, callback_data=f"fb|help|{k}") for n, k in names[3:]]]
    return M(rows)


def start_text(name: str) -> str:
    return (f"⚡ <b>Hey, I'm {esc(name)}</b>\n\nA stream-first music bot for Telegram voice chats: songs start in a "
            "second or two, the next one is prepared while you listen, and broken streams heal themselves.\n\n"
            "Add me to a group, start a voice chat and send <code>/play song name</code>.")


def start_kb(username: str) -> M:
    rows = [[B("➕ Add me to your group", url=f"https://t.me/{username}?startgroup=true&admin=invite_users+manage_video_chats+delete_messages")],
            [B("📖 Commands", callback_data="fb|help|play")]]
    extra = []
    if config.SUPPORT_CHAT:
        extra.append(B("💬 Support", url=config.SUPPORT_CHAT))
    if config.SUPPORT_CHANNEL:
        extra.append(B("📢 Updates", url=config.SUPPORT_CHANNEL))
    if extra:
        rows.append(extra)
    return M(rows)
