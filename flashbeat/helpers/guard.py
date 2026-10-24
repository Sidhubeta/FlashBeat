from typing import Optional

from flashbeat import runtime as rt
from flashbeat.core.models import Track
from flashbeat.helpers import ui


async def control_guard(m) -> Optional[Track]:
    """Reply with a reason and return None unless the sender may control the current track."""
    chat_id = m.chat.id
    track = rt.player.current(chat_id)
    if not track or not rt.player.is_active(chat_id):
        await m.reply_text(ui.NOTHING_PLAYING)
        return None
    if not await rt.perms.can_control(chat_id, rt.perms.user_id(m), track):
        await m.reply_text(ui.NOT_ALLOWED)
        return None
    return track


async def admin_guard(m) -> bool:
    if not await rt.perms.privileged(m.chat.id, rt.perms.user_id(m)):
        await m.reply_text("👮 Admins only.")
        return False
    return True


def arg(m) -> str:
    parts = (m.text or "").split(None, 1)
    return parts[1].strip() if len(parts) > 1 else ""
