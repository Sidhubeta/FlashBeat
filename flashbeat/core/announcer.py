"""Sends / edits the now-playing messages. Text goes out instantly, the cover card follows."""
from __future__ import annotations

import asyncio
from typing import Optional

from pyrogram.types import InputMediaPhoto

from flashbeat import logger
from flashbeat.config import config
from flashbeat.core.models import Track
from flashbeat.helpers import ui


class Announcer:
    def __init__(self, bot, cards, store, queue) -> None:
        self.bot, self.cards, self.store, self.queue = bot, cards, store, queue
        self.player = None                      # set by runtime
        self._bg: set[asyncio.Task] = set()

    def _spawn(self, coro) -> None:
        t = asyncio.ensure_future(coro)
        self._bg.add(t)
        t.add_done_callback(self._bg.discard)

    def _state(self, chat_id: int, track: Track) -> tuple[str, object]:
        q = self.queue.get(chat_id)
        paused = self.player.is_paused(chat_id) if self.player else False
        auto = self.store.setting(chat_id, "autoplay")
        return (ui.now_playing(track, paused=paused, loop=q.loop, autoplay=auto),
                ui.controls(paused, q.loop, auto))

    async def now_playing(self, chat_id: int, track: Track, status=None) -> None:
        text, kb = self._state(chat_id, track)
        card = self.cards.cached(track) if config.THUMB_GEN else None
        msg = None
        try:
            if card:
                msg = await self._photo(chat_id, card, text, kb, status)
            else:
                msg = await self._text(chat_id, text, kb, status)
                if config.THUMB_GEN and track.thumbnail and msg:
                    self._spawn(self._upgrade(chat_id, track, msg))
        except Exception as ex:
            logger.warning("announce failed in %s: %s", chat_id, ex)
        if msg:
            track.message_id = msg.id

    async def _text(self, chat_id, text, kb, status):
        if status:
            try:
                return await status.edit_text(text, reply_markup=kb)
            except Exception:
                pass
        return await self.bot.send_message(chat_id, text, reply_markup=kb)

    async def _photo(self, chat_id, path, text, kb, status):
        if status:
            try:
                return await status.edit_media(InputMediaPhoto(path, caption=text), reply_markup=kb)
            except Exception:
                try:
                    await status.delete()
                except Exception:
                    pass
        return await self.bot.send_photo(chat_id, path, caption=text, reply_markup=kb)

    async def _upgrade(self, chat_id: int, track: Track, msg) -> None:
        path = await self.cards.render(track)
        if not path or self.queue.get(chat_id).current is not track:
            return
        text, kb = self._state(chat_id, track)
        try:
            new = await self._photo(chat_id, path, text, kb, msg)
        except Exception as ex:
            logger.debug("card upgrade failed: %s", ex)
            return
        if self.queue.get(chat_id).current is track:
            track.message_id = new.id
        else:                                    # track ended while we rendered: drop the orphan
            try:
                await new.delete()
            except Exception:
                pass

    async def refresh(self, chat_id: int) -> None:
        q = self.queue.peek(chat_id)
        track = q.current if q else None
        if not track or not track.message_id:
            return
        text, kb = self._state(chat_id, track)
        try:
            try:
                await self.bot.edit_message_caption(chat_id, track.message_id, text, reply_markup=kb)
            except Exception:
                await self.bot.edit_message_text(chat_id, track.message_id, text, reply_markup=kb)
        except Exception:
            pass

    async def finished(self, chat_id: int, track: Track) -> None:
        if track.message_id:
            mid, track.message_id = track.message_id, 0
            try:
                await self.bot.delete_messages(chat_id, mid)
            except Exception:
                pass

    async def failed(self, chat_id: int, track: Track, status=None) -> None:
        try:
            if status:
                await status.edit_text(ui.failed(track))
            else:
                await self.bot.send_message(chat_id, ui.failed(track))
        except Exception:
            pass

    async def notice(self, chat_id: int, kind: str) -> None:
        try:
            await self.bot.send_message(chat_id, ui.NOTICES.get(kind, kind))
        except Exception:
            pass
