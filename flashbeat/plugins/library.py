from pyrogram import filters, types

from flashbeat import runtime as rt
from flashbeat.core import lyrics as lyr
from flashbeat.core.models import Track
from flashbeat.helpers import text as T
from flashbeat.helpers import ui
from flashbeat.helpers.filters import not_banned
from flashbeat.helpers.flow import add_and_play
from flashbeat.helpers.guard import admin_guard, arg

bot = rt.bot
G = filters.group & not_banned


async def send_lyrics(chat_id: int, reply_to, query: str, channel: str = "") -> None:
    status = await bot.send_message(chat_id, f"🎤 Looking up lyrics for <i>{T.esc(T.short(query, 50))}</i> …")
    found = await lyr.fetch(rt.resolver.session, query, channel)
    if not found:
        return await status.edit_text("😶 No lyrics found for that. Try <code>/lyrics artist - title</code>.")
    pages = lyr.paginate(found["text"])
    token = T.token()
    rt.tokens.set(token, ("lyrics", found, pages))
    head = f"🎤 <b>{T.esc(found['title'])}</b> - {T.esc(found['artist'])}\n\n"
    await status.edit_text(head + T.esc(pages[0]), reply_markup=ui.lyrics_kb(token, 0, len(pages)))


@bot.on_message(filters.command("lyrics") & G)
async def lyrics_cmd(_, m: types.Message):
    q = arg(m)
    track = rt.player.current(m.chat.id)
    if not q and not track:
        return await m.reply_text("Usage: <code>/lyrics song name</code> (or use it while a song plays)")
    await send_lyrics(m.chat.id, m, q or track.title, "" if q else track.channel)


@bot.on_message(filters.command(["fav", "save"]) & G)
async def fav_cmd(_, m: types.Message):
    track = rt.player.current(m.chat.id)
    if not track or not m.from_user:
        return await m.reply_text(ui.NOTHING_PLAYING)
    if track.source == "telegram":
        return await m.reply_text("Telegram files can't be saved to favourites.")
    added = rt.store.toggle_fav(m.from_user.id, track.to_doc())
    await m.reply_text("❤️ Saved to your favourites." if added else "💔 Removed from your favourites.")


@bot.on_message(filters.command("favs") & G)
async def favs_cmd(_, m: types.Message):
    items = rt.store.favs(m.from_user.id) if m.from_user else []
    await m.reply_text(ui.favs_text(items), reply_markup=ui.favs_kb(len(items)) if items else None)


@bot.on_message(filters.command("playfav") & G)
async def playfav_cmd(_, m: types.Message):
    if not m.from_user:
        return
    items = rt.store.favs(m.from_user.id)
    if not items:
        return await m.reply_text(ui.favs_text([]))
    a = arg(m)
    picked = items[:20] if a in ("", "all") else ([items[int(a) - 1]] if a.isdigit() and 1 <= int(a) <= len(items) else [])
    if not picked:
        return await m.reply_text("Usage: <code>/playfav [number|all]</code>")
    status = await m.reply_text(ui.searching())
    await add_and_play(m.chat, m.from_user.mention, m.from_user.id, [Track.from_doc(d) for d in picked], status)


@bot.on_message(filters.command("history") & G)
async def history_cmd(_, m: types.Message):
    rows = rt.store.recent(m.chat.id, 10)
    if not rows:
        return await m.reply_text("📜 Nothing played here yet.")
    await m.reply_text("📜 <b>Recently played</b>\n\n" + "\n".join(
        f"<b>{i}.</b> {T.esc(T.short(r['title'], 55))}" for i, r in enumerate(rows, 1)))


@bot.on_message(filters.command(["top", "toptracks"]) & G)
async def top_cmd(_, m: types.Message):
    rows = rt.store.top(m.chat.id, 10)
    if not rows:
        return await m.reply_text("🏆 No plays recorded yet.")
    await m.reply_text("🏆 <b>Most played in this chat</b>\n\n" + "\n".join(
        f"<b>{i}.</b> {T.esc(T.short(r['title'], 50))} · {r['n']}×" for i, r in enumerate(rows, 1)))


@bot.on_message(filters.command("autoplay") & G)
async def autoplay_cmd(_, m: types.Message):
    a = arg(m).lower()
    cur = rt.store.setting(m.chat.id, "autoplay")
    if a in ("on", "off"):
        if not await admin_guard(m):
            return
        if (a == "on") != cur:
            await rt.player.toggle_autoplay(m.chat.id)
        cur = a == "on"
        return await m.reply_text(f"🤖 Autoplay is now <b>{'on' if cur else 'off'}</b>.")
    await m.reply_text(f"🤖 Autoplay is <b>{'on' if cur else 'off'}</b>. Change it with <code>/autoplay on|off</code> (admins).")
