"""Shared "get these tracks playing" flow used by /play, /search picks, favourites, history."""
from __future__ import annotations

import asyncio
import time
from typing import Optional

from pyrogram import enums, errors

from flashbeat import logger
from flashbeat import runtime as rt
from flashbeat.config import config
from flashbeat.core.models import Track
from flashbeat.helpers import ui


async def ensure_assistant(chat) -> Optional[str]:
    """Make sure the assistant account is in the chat. Returns an error text or None."""
    chat_id = chat.id
    if rt.joined.get(chat_id):
        return None
    ub = rt.assistants.pick(chat_id)
    try:
        member = await rt.bot.get_chat_member(chat_id, ub.me.id)
        if member.status in (enums.ChatMemberStatus.BANNED, enums.ChatMemberStatus.RESTRICTED):
            try:
                await rt.bot.unban_chat_member(chat_id, ub.me.id)
            except Exception:
                return (f"🚫 My assistant ({ub.me.mention}) is banned/restricted here. "
                        "Please unban it and try again.")
        rt.joined.set(chat_id, True)
        return None
    except errors.ChatAdminRequired:
        return ui.ADMIN_REQUIRED
    except errors.UserNotParticipant:
        pass
    except Exception as ex:
        logger.warning("membership check failed in %s: %s", chat_id, ex)

    try:
        link = chat.username or (await rt.bot.get_chat(chat_id)).invite_link or await rt.bot.export_chat_invite_link(chat_id)
    except errors.ChatAdminRequired:
        return ui.ADMIN_REQUIRED
    except Exception as ex:
        return f"⚠️ Couldn't create an invite link ({type(ex).__name__})."
    try:
        await ub.join_chat(link)
    except errors.UserAlreadyParticipant:
        pass
    except errors.InviteRequestSent:
        try:
            await rt.bot.approve_chat_join_request(chat_id, ub.me.id)
        except errors.HideRequesterMissing:
            pass
        except Exception as ex:
            return f"⚠️ Couldn't approve my assistant ({type(ex).__name__})."
    except Exception as ex:
        logger.error("assistant join failed in %s: %s", chat_id, ex)
        return f"⚠️ My assistant couldn't join ({type(ex).__name__})."
    rt.joined.set(chat_id, True)
    return None


async def add_and_play(chat, user_mention: str, user_id: int, tracks: list[Track], status,
                       mode: str = "end", t0: Optional[float] = None) -> None:
    """Queue `tracks` (first one honours `mode`) and start playback when idle."""
    chat_id = chat.id
    q = rt.queue.get(chat_id)
    room = max(0, config.QUEUE_LIMIT - len(q))
    if mode == "end" and room == 0:
        await status.edit_text(ui.QUEUE_FULL.format(config.QUEUE_LIMIT))
        return
    for t in tracks:
        t.requested_by, t.requested_by_id = user_mention, user_id
    first, rest = tracks[0], tracks[1:]
    if first.source not in ("telegram",):
        rt.resolver.prefetch(first)                      # resolve while the assistant joins
    err = await ensure_assistant(chat)
    if err:
        await status.edit_text(err)
        return

    wait = sum(t.duration for t in q.all())
    pos = rt.player.enqueue(chat_id, first, mode)
    added = 0
    for t in rest[: max(0, room - (1 if mode == "end" else 0))]:
        rt.player.enqueue(chat_id, t, "end")
        added += 1

    if pos == 0:
        ok = await rt.player.start(chat_id, status)
        if ok and t0:
            rt.latency.append(time.perf_counter() - t0)
            logger.info("audio started %.2fs after the command (%s)", rt.latency[-1], first.id)
    else:
        await status.edit_text(ui.queued(first, pos, wait), reply_markup=ui.queued_kb(first.id))
    if added:
        try:
            await rt.bot.send_message(chat_id, ui.playlist_added(added))
        except Exception:
            pass
