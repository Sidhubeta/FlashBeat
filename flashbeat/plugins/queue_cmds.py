from pyrogram import filters, types

from flashbeat import runtime as rt
from flashbeat.helpers import text as T
from flashbeat.helpers import ui
from flashbeat.helpers.filters import not_banned
from flashbeat.helpers.guard import arg, control_guard

bot = rt.bot
G = filters.group & not_banned


@bot.on_message(filters.command(["queue", "q"]) & G)
async def queue_cmd(_, m: types.Message):
    q = rt.queue.peek(m.chat.id)
    if not q or not q.current:
        return await m.reply_text(ui.NOTHING_PLAYING)
    text, page = ui.queue_text(q, 0)
    await m.reply_text(text, reply_markup=ui.queue_kb(page, ui.pages_total(q)))


@bot.on_message(filters.command(["np", "nowplaying", "current"]) & G)
async def np_cmd(_, m: types.Message):
    track = rt.player.current(m.chat.id)
    if not track:
        return await m.reply_text(ui.NOTHING_PLAYING)
    q = rt.queue.get(m.chat.id)
    text = ui.now_playing(track, paused=rt.player.is_paused(m.chat.id), loop=q.loop,
                          autoplay=rt.store.setting(m.chat.id, "autoplay"))
    await m.reply_text(f"{text}\n\n{ui.progress(track)}", reply_markup=ui.controls(
        rt.player.is_paused(m.chat.id), q.loop, rt.store.setting(m.chat.id, "autoplay")))


@bot.on_message(filters.command("shuffle") & G)
async def shuffle_cmd(_, m: types.Message):
    if await control_guard(m):
        n = rt.queue.get(m.chat.id).shuffle()
        await m.reply_text(f"🔀 Shuffled {n} upcoming track{'s' if n != 1 else ''}." if n else "Nothing to shuffle.")


@bot.on_message(filters.command("remove") & G)
async def remove_cmd(_, m: types.Message):
    if not await control_guard(m):
        return
    a = arg(m)
    if not a.isdigit():
        return await m.reply_text("Usage: <code>/remove 2</code> (position from /queue)")
    t = rt.queue.get(m.chat.id).remove(int(a))
    await m.reply_text(f"🗑 Removed <b>{T.esc(T.short(t.title, 50))}</b>" if t else "No track at that position.")


@bot.on_message(filters.command("move") & G)
async def move_cmd(_, m: types.Message):
    if not await control_guard(m):
        return
    bits = arg(m).split()
    if len(bits) != 2 or not all(b.isdigit() for b in bits):
        return await m.reply_text("Usage: <code>/move 4 1</code> (from, to)")
    t = rt.queue.get(m.chat.id).move(int(bits[0]), int(bits[1]))
    await m.reply_text(f"↕️ Moved <b>{T.esc(T.short(t.title, 50))}</b> to #{bits[1]}" if t else "Invalid positions.")


@bot.on_message(filters.command(["clear", "clearqueue"]) & G)
async def clear_cmd(_, m: types.Message):
    if await control_guard(m):
        n = rt.queue.get(m.chat.id).clear_upcoming()
        await m.reply_text(f"🧹 Cleared {n} queued track{'s' if n != 1 else ''}.")
