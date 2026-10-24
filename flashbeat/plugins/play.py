import os
import time

from pyrogram import filters, types

from flashbeat import logger
from flashbeat import runtime as rt
from flashbeat.config import config
from flashbeat.core.models import Track
from flashbeat.core.resolver import DOWNLOAD_DIR, ResolveError
from flashbeat.helpers import text as T
from flashbeat.helpers import ui
from flashbeat.helpers.filters import not_banned
from flashbeat.helpers.flow import add_and_play

bot = rt.bot


def _media(msg):
    return msg and (msg.audio or msg.voice or msg.video or msg.document or msg.video_note)


async def _telegram_track(reply: types.Message, status: types.Message) -> Track | None:
    media = _media(reply)
    mime = getattr(media, "mime_type", "") or ""
    if getattr(media, "file_size", 0) > config.TG_FILE_LIMIT_MB * 1024 * 1024:
        await status.edit_text(f"📦 That file is over the {config.TG_FILE_LIMIT_MB} MB limit.")
        return None
    if not (mime.startswith(("audio/", "video/")) or reply.audio or reply.voice or reply.video):
        await status.edit_text("🙅 That file isn't audio or video.")
        return None
    duration = getattr(media, "duration", 0) or 0
    if duration > config.DURATION_LIMIT:
        await status.edit_text(ui.too_long(config.DURATION_LIMIT // 60))
        return None
    ext = (getattr(media, "file_name", "") or "").rsplit(".", 1)[-1] if "." in (getattr(media, "file_name", "") or "") else "bin"
    path = os.path.join(DOWNLOAD_DIR, f"tg_{media.file_unique_id}.{ext}")
    if not os.path.exists(path):
        await status.edit_text("📥 Downloading your file …")
        await reply.download(file_name=path)
    title = getattr(media, "title", None) or getattr(media, "file_name", None) or "Telegram file"
    return Track(id=f"tg{media.file_unique_id}", title=title, url=reply.link or "", duration=duration,
                 channel=getattr(media, "performer", "") or "", source="telegram", file_path=path,
                 video=mime.startswith("video/") and config.VIDEO_PLAY)


@bot.on_message(filters.command(["play", "vplay", "playforce", "vplayforce", "playnext"]) & filters.group & not_banned)
async def play_cmd(_, m: types.Message):
    t0 = time.perf_counter()
    uid = rt.perms.user_id(m)
    if not m.from_user:
        return await m.reply_text(ui.ANON_ADMIN)
    chat_id = m.chat.id
    cmd = m.command[0].lower()
    parts = (m.text or "").split(None, 1)
    flags, query = T.parse_flags(parts[1].split() if len(parts) > 1 else [])
    video = (cmd.startswith("v") or "v" in flags) and config.VIDEO_PLAY
    mode = "force" if (cmd.endswith("force") or "f" in flags) else ("next" if (cmd == "playnext" or "n" in flags) else "end")

    reply = m.reply_to_message
    url = T.message_url(m)
    has_file = bool(reply and _media(reply) and not url)
    if not (has_file or url or query):
        return await m.reply_text(ui.USAGE_PLAY)

    status = await m.reply_text(ui.searching(query))            # instant feedback, before any checks
    if not await rt.perms.can_play(chat_id, uid):
        return await status.edit_text("👮 Only admins can use /play in this chat.")
    if mode == "force" and not await rt.perms.privileged(chat_id, uid):
        return await status.edit_text(ui.NOT_ALLOWED)
    if rt.store.setting(chat_id, "delete_cmds"):
        try:
            await m.delete()
        except Exception:
            pass

    try:
        if has_file:
            track = await _telegram_track(reply, status)
            tracks = [track] if track else []
        elif url:
            await status.edit_text("🔗 Opening link …")
            tracks = await rt.resolver.from_url(url, video)
        else:
            tracks = await rt.resolver.search(query, 1, video)
    except ResolveError as ex:
        logger.info("lookup failed: %s", ex)
        return await status.edit_text(ui.not_found())
    if has_file and not tracks:
        return
    if not tracks:
        return await status.edit_text(ui.not_found())
    if tracks[0].duration > config.DURATION_LIMIT:
        return await status.edit_text(ui.too_long(config.DURATION_LIMIT // 60))
    tracks = [t for t in tracks if t.duration <= config.DURATION_LIMIT]

    await add_and_play(m.chat, m.from_user.mention, m.from_user.id, tracks, status, mode, t0)


@bot.on_message(filters.command(["search", "find"]) & filters.group & not_banned)
async def search_cmd(_, m: types.Message):
    parts = (m.text or "").split(None, 1)
    if len(parts) < 2:
        return await m.reply_text("🔎 Usage: <code>/search song name</code>")
    query = parts[1].strip()
    status = await m.reply_text(ui.searching(query))
    try:
        results = await rt.resolver.search(query, 6)
    except ResolveError:
        results = []
    if not results:
        return await status.edit_text(ui.not_found())
    token = T.token()
    rt.tokens.set(token, ("search", m.from_user.id if m.from_user else 0, results))
    await status.edit_text(ui.search_results(query, results), reply_markup=ui.search_kb(token, len(results)))
