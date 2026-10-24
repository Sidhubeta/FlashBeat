from pyrogram import filters, types

from flashbeat import runtime as rt
from flashbeat.helpers import ui
from flashbeat.helpers.filters import not_banned
from flashbeat.helpers.guard import admin_guard

bot = rt.bot
G = filters.group & not_banned


async def _target(m: types.Message):
    if m.reply_to_message and m.reply_to_message.from_user:
        return m.reply_to_message.from_user
    parts = (m.text or "").split()
    if len(parts) > 1:
        try:
            return await bot.get_users(int(parts[1]) if parts[1].lstrip("-").isdigit() else parts[1])
        except Exception:
            return None
    return None


@bot.on_message(filters.command("auth") & G)
async def auth_cmd(_, m: types.Message):
    if not await admin_guard(m):
        return
    user = await _target(m)
    if not user:
        return await m.reply_text("Reply to a user or send <code>/auth user_id</code>.")
    if user.id in await rt.perms.admins(m.chat.id):
        return await m.reply_text("They're already an admin.")
    rt.store.add_auth(m.chat.id, user.id)
    await m.reply_text(f"✅ {user.mention} can now control playback.")


@bot.on_message(filters.command("unauth") & G)
async def unauth_cmd(_, m: types.Message):
    if not await admin_guard(m):
        return
    user = await _target(m)
    if not user:
        return await m.reply_text("Reply to a user or send <code>/unauth user_id</code>.")
    rt.store.rm_auth(m.chat.id, user.id)
    await m.reply_text(f"🚫 {user.mention} can no longer control playback.")


@bot.on_message(filters.command("authlist") & G)
async def authlist_cmd(_, m: types.Message):
    ids = rt.store.auth_users(m.chat.id)
    if not ids:
        return await m.reply_text("No authorised users in this chat.")
    await m.reply_text("✅ <b>Authorised users</b>\n\n" + "\n".join(f"• <a href='tg://user?id={i}'>{i}</a>" for i in ids))


@bot.on_message(filters.command("reload") & G)
async def reload_cmd(_, m: types.Message):
    ids = await rt.perms.admins(m.chat.id, reload=True)
    await m.reply_text(f"🔄 Admin list refreshed ({len(ids)} admins).")


@bot.on_message(filters.command("settings") & G)
async def settings_cmd(_, m: types.Message):
    if not await admin_guard(m):
        return
    s = rt.store.chat(m.chat.id)
    await m.reply_text(ui.settings_text(s), reply_markup=ui.settings_kb(s))
