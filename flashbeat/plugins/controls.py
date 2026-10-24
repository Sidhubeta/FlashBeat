from pyrogram import filters, types

from flashbeat import runtime as rt
from flashbeat.core.queue import LOOP_NAMES, LOOP_OFF, LOOP_QUEUE, LOOP_TRACK
from flashbeat.helpers import text as T
from flashbeat.helpers import ui
from flashbeat.helpers.filters import not_banned
from flashbeat.helpers.guard import arg, control_guard

bot = rt.bot
G = filters.group & not_banned


@bot.on_message(filters.command("pause") & G)
async def pause_cmd(_, m: types.Message):
    if await control_guard(m):
        await m.reply_text("⏸ Paused." if await rt.player.pause(m.chat.id) else "Already paused.")


@bot.on_message(filters.command("resume") & G)
async def resume_cmd(_, m: types.Message):
    if await control_guard(m):
        await m.reply_text("▶️ Resumed." if await rt.player.resume(m.chat.id) else "It isn't paused.")


@bot.on_message(filters.command(["skip", "next"]) & G)
async def skip_cmd(_, m: types.Message):
    if await control_guard(m):
        nxt = await rt.player.skip(m.chat.id)
        await m.reply_text(f"⏭ Skipped. Up next: <b>{T.esc(T.short(nxt.title, 50))}</b>" if nxt
                           else "⏭ Skipped - the queue is empty.")


@bot.on_message(filters.command(["stop", "end"]) & G)
async def stop_cmd(_, m: types.Message):
    if await control_guard(m):
        await rt.player.stop(m.chat.id)
        await m.reply_text("⏹ Stopped and left the voice chat.")


async def _seek(m: types.Message, sign: int):
    if not await control_guard(m):
        return
    a = arg(m)
    secs = T.parse_time(a) if a else 10
    if secs <= 0:
        return await m.reply_text("Usage: <code>/seek 30</code> (seconds)")
    res = await rt.player.seek(m.chat.id, delta=sign * secs)
    await m.reply_text("⚠️ This track can't be seeked." if res is None else
                       "⚠️ That's beyond the end of the track." if res == -1 else f"⏩ Now at <b>{T.fmt_time(res)}</b>")


@bot.on_message(filters.command("seek") & G)
async def seek_cmd(_, m):
    await _seek(m, 1)


@bot.on_message(filters.command("seekback") & G)
async def seekback_cmd(_, m):
    await _seek(m, -1)


@bot.on_message(filters.command("seekto") & G)
async def seekto_cmd(_, m: types.Message):
    if not await control_guard(m):
        return
    pos = T.parse_time(arg(m))
    res = await rt.player.seek(m.chat.id, to=pos)
    await m.reply_text("⚠️ Can't seek here." if res is None else
                       "⚠️ That's beyond the end of the track." if res == -1 else f"⏩ Jumped to <b>{T.fmt_time(res)}</b>")


@bot.on_message(filters.command("loop") & G)
async def loop_cmd(_, m: types.Message):
    if not await control_guard(m):
        return
    a = arg(m).lower()
    mapping = {"off": LOOP_OFF, "0": LOOP_OFF, "track": LOOP_TRACK, "1": LOOP_TRACK, "song": LOOP_TRACK,
               "queue": LOOP_QUEUE, "all": LOOP_QUEUE, "2": LOOP_QUEUE}
    cur = rt.queue.get(m.chat.id).loop
    mode = mapping.get(a, (cur + 1) % 3)
    await rt.player.set_loop(m.chat.id, mode)
    await m.reply_text(f"🔁 Loop: <b>{LOOP_NAMES[mode]}</b>")


@bot.on_message(filters.command(["volume", "vol"]) & G)
async def volume_cmd(_, m: types.Message):
    if not await control_guard(m):
        return
    a = arg(m)
    if not a.isdigit() or not 1 <= int(a) <= 200:
        return await m.reply_text(f"🔊 Volume is <b>{rt.store.setting(m.chat.id, 'volume')}%</b>. Usage: <code>/volume 1-200</code>")
    ok = await rt.player.set_volume(m.chat.id, int(a))
    await m.reply_text(f"🔊 Volume set to <b>{a}%</b>" if ok else "⚠️ Volume control isn't available in this setup.")
