from pyrogram import filters, types

from flashbeat import runtime as rt
from flashbeat.config import config
from flashbeat.helpers import ui
from flashbeat.helpers.filters import not_banned

bot = rt.bot


async def _log(text: str) -> None:
    try:
        await bot.send_message(config.LOGGER_ID, text)
    except Exception:
        pass


@bot.on_message(filters.command(["start", "help"]) & ~filters.bot & not_banned)
async def start_cmd(_, m: types.Message):
    if m.chat.type.name == "PRIVATE":
        if rt.store.register_user(m.from_user.id):
            await _log(f"👤 New user: {m.from_user.mention} (<code>{m.from_user.id}</code>)")
        if m.command[0] == "help":
            return await m.reply_text(ui.HELP["play"], reply_markup=ui.help_kb("play"))
        text, kb = ui.start_text(bot.display_name), ui.start_kb(bot.username)
        if config.START_IMG:
            try:
                return await m.reply_photo(config.START_IMG, caption=text, reply_markup=kb)
            except Exception:
                pass
        return await m.reply_text(text, reply_markup=kb)
    rt.store.register_chat(m.chat.id)
    if m.command[0] == "help":
        return await m.reply_text(ui.HELP["play"], reply_markup=ui.help_kb("play"))
    await m.reply_text("⚡ <b>FlashBeat is ready.</b> Start a voice chat and send <code>/play song name</code>.\n"
                       "/help for all commands.")


@bot.on_message(filters.new_chat_members)
async def joined(_, m: types.Message):
    if any(u.id == bot.id for u in m.new_chat_members):
        if rt.store.register_chat(m.chat.id):
            await _log(f"➕ Added to <b>{m.chat.title}</b> (<code>{m.chat.id}</code>)")
        await m.reply_text("⚡ Thanks for adding me! Make me admin with <b>Invite users via link</b>, "
                           "start a voice chat and use /play.")
