from pyrogram import filters

from flashbeat import runtime as rt
from flashbeat.config import config


# async on purpose: pyrogram runs *sync* custom filters in a thread pool, which is slower
async def _not_banned(_, __, update) -> bool:
    chat = getattr(update, "chat", None) or getattr(getattr(update, "message", None), "chat", None)
    user = getattr(update, "from_user", None)
    ids = {getattr(user, "id", 0), getattr(chat, "id", 0)}
    return not (ids & rt.store.blacklist)


async def _is_sudo(_, __, update) -> bool:
    user = getattr(update, "from_user", None)
    return bool(user and (user.id == config.OWNER_ID or user.id in rt.store.sudoers))


not_banned = filters.create(_not_banned, "NotBanned")
sudo_only = filters.create(_is_sudo, "SudoOnly")
groups = filters.group
