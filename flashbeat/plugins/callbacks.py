from pyrogram import filters, types

from flashbeat import runtime as rt
from flashbeat.core.models import Track
from flashbeat.core.queue import LOOP_NAMES
from flashbeat.helpers import text as T
from flashbeat.helpers import ui
from flashbeat.helpers.filters import not_banned
from flashbeat.helpers.flow import add_and_play
from flashbeat.plugins.library import send_lyrics

bot = rt.bot


async def _allowed(q: types.CallbackQuery, track=None) -> bool:
    if await rt.perms.can_control(q.message.chat.id, q.from_user.id, track):
        return True
    await q.answer("Only admins or the requester can do that.", show_alert=True)
    return False


@bot.on_callback_query(filters.regex(r"^fb\|") & not_banned)
async def router(_, q: types.CallbackQuery):
    parts = q.data.split("|")
    action, args = parts[1], parts[2:]
    chat_id = q.message.chat.id
    p = rt.player
    track = p.current(chat_id)

    if action == "close":
        await q.answer()
        return await q.message.delete()

    if action == "help":
        key = args[0]
        await q.answer()
        return await q.message.edit_text(ui.HELP.get(key, ui.HELP["play"]), reply_markup=ui.help_kb(key))

    if action in ("pause", "skip", "stop", "loop", "shuffle", "auto", "playnow", "rm"):
        if not track or not p.is_active(chat_id):
            return await q.answer("Nothing is playing.", show_alert=True)
        if not await _allowed(q, track):
            return
        if action == "pause":
            await q.answer()
            if p.is_paused(chat_id):
                await p.resume(chat_id)
            else:
                await p.pause(chat_id)
        elif action == "skip":
            await q.answer("⏭ Skipping …")
            await p.skip(chat_id)
        elif action == "stop":
            await q.answer("⏹ Stopped")
            await p.stop(chat_id)
        elif action == "loop":
            mode = (rt.queue.get(chat_id).loop + 1) % 3
            await p.set_loop(chat_id, mode)
            await q.answer(f"Loop: {LOOP_NAMES[mode]}")
        elif action == "shuffle":
            n = rt.queue.get(chat_id).shuffle()
            await q.answer(f"Shuffled {n} tracks" if n else "Nothing to shuffle")
        elif action == "auto":
            on = await p.toggle_autoplay(chat_id)
            await q.answer(f"Autoplay {'on' if on else 'off'}")
        elif action == "playnow":
            queue = rt.queue.get(chat_id)
            idx, t = queue.find(args[0])
            if not t or idx < 1:
                return await q.answer("Already playing or gone.", show_alert=True)
            queue.remove(idx)
            await q.answer("▶️ Playing now")
            queue.force(t)
            await q.message.delete()
            await p.start(chat_id)
        elif action == "rm":
            queue = rt.queue.get(chat_id)
            idx, t = queue.find(args[0])
            if idx < 1:
                return await q.answer("Already gone.", show_alert=True)
            queue.remove(idx)
            await q.answer("Removed")
            await q.message.edit_text("🗑 Removed from the queue.")
        return

    if action == "fav":
        if not track:
            return await q.answer("Nothing is playing.", show_alert=True)
        if track.source == "telegram":
            return await q.answer("Telegram files can't be saved.", show_alert=True)
        added = rt.store.toggle_fav(q.from_user.id, track.to_doc())
        return await q.answer("❤️ Saved" if added else "💔 Removed")

    if action == "lyrics":
        if not track:
            return await q.answer("Nothing is playing.", show_alert=True)
        await q.answer("🎤 Fetching …")
        return await send_lyrics(chat_id, q.message, track.title, track.channel)

    if action == "lyr":
        data = rt.tokens.get(args[0])
        if not data or data[0] != "lyrics":
            return await q.answer("This lyrics view expired.", show_alert=True)
        _, found, pages = data
        page = min(max(int(args[1]), 0), len(pages) - 1)
        await q.answer()
        head = f"🎤 <b>{T.esc(found['title'])}</b> - {T.esc(found['artist'])}\n\n"
        return await q.message.edit_text(head + T.esc(pages[page]), reply_markup=ui.lyrics_kb(args[0], page, len(pages)))

    if action in ("queue", "qp"):
        queue = rt.queue.peek(chat_id)
        if not queue or not queue.current:
            return await q.answer("Nothing is playing.", show_alert=True)
        text, page = ui.queue_text(queue, int(args[0]))
        kb = ui.queue_kb(page, ui.pages_total(queue))
        await q.answer()
        try:
            if action == "queue":            # from the now-playing card: send a separate message
                await q.message.reply_text(text, reply_markup=kb)
            else:                            # page navigation: edit in place
                await q.message.edit_text(text, reply_markup=kb)
        except Exception:
            pass
        return

    if action == "pick":
        data = rt.tokens.get(args[0])
        if not data or data[0] != "search":
            return await q.answer("These results expired - search again.", show_alert=True)
        _, owner, results = data
        if q.from_user.id != owner and not await rt.perms.privileged(chat_id, q.from_user.id):
            return await q.answer("Only the person who searched can pick.", show_alert=True)
        if not await rt.perms.can_play(chat_id, q.from_user.id):
            return await q.answer("Only admins can play in this chat.", show_alert=True)
        from dataclasses import replace
        chosen = replace(results[int(args[1])])
        await q.answer("⚡ On it")
        await q.message.edit_text(ui.searching())
        return await add_and_play(q.message.chat, q.from_user.mention, q.from_user.id, [chosen], q.message)

    if action == "favplay":
        items = rt.store.favs(q.from_user.id)
        if not items:
            return await q.answer("No saved songs.", show_alert=True)
        if not await rt.perms.can_play(chat_id, q.from_user.id):
            return await q.answer("Only admins can play in this chat.", show_alert=True)
        picked = items[:20] if args[0] == "all" else items[int(args[0]):int(args[0]) + 1]
        await q.answer("⚡ On it")
        await q.message.edit_text(ui.searching())
        return await add_and_play(q.message.chat, q.from_user.mention, q.from_user.id,
                                  [Track.from_doc(d) for d in picked], q.message)

    if action == "set":
        if not await rt.perms.privileged(chat_id, q.from_user.id):
            return await q.answer("Admins only.", show_alert=True)
        key = args[0]
        if key not in ("admin_only", "delete_cmds", "autoplay"):
            return await q.answer()
        if key == "autoplay":
            await p.toggle_autoplay(chat_id)
        else:
            rt.store.set_chat(chat_id, **{key: not rt.store.setting(chat_id, key)})
        s = rt.store.chat(chat_id)
        await q.answer("Saved")
        return await q.message.edit_text(ui.settings_text(s), reply_markup=ui.settings_kb(s))

    await q.answer()
