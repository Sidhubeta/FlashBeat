from __future__ import annotations

from typing import Optional

from pyrogram import enums

from flashbeat import logger
from flashbeat.config import config
from flashbeat.core.cache import SingleFlight, TTLCache


class Perms:
    def __init__(self, bot, store) -> None:
        self.bot, self.store = bot, store
        self.cache = TTLCache(1024, ttl=600)
        self.flight = SingleFlight()

    async def admins(self, chat_id: int, reload: bool = False) -> set[int]:
        if not reload:
            hit = self.cache.get(chat_id)
            if hit is not None:
                return hit

        async def load() -> set[int]:
            ids: set[int] = set()
            try:
                async for m in self.bot.get_chat_members(chat_id, filter=enums.ChatMembersFilter.ADMINISTRATORS):
                    if m.user:
                        ids.add(m.user.id)
            except Exception as ex:
                logger.warning("admin list failed for %s: %s", chat_id, ex)
                return self.cache.get(chat_id) or set()
            self.cache.set(chat_id, ids)
            return ids

        return await self.flight.do(("adm", chat_id), load)

    def is_sudo(self, user_id: int) -> bool:
        return user_id == config.OWNER_ID or user_id in self.store.sudoers

    @staticmethod
    def user_id(m) -> Optional[int]:
        """Real user id; for anonymous admins (sender_chat == chat) returns the chat id."""
        if m.from_user:
            return m.from_user.id
        if m.sender_chat and m.sender_chat.id == m.chat.id:
            return m.chat.id
        return None

    async def privileged(self, chat_id: int, user_id: Optional[int]) -> bool:
        if user_id is None:
            return False
        if user_id == chat_id or self.is_sudo(user_id) or self.store.is_auth(chat_id, user_id):
            return True
        return user_id in await self.admins(chat_id)

    async def can_play(self, chat_id: int, user_id: Optional[int]) -> bool:
        if user_id is None:
            return False
        if not self.store.setting(chat_id, "admin_only"):
            return True
        return await self.privileged(chat_id, user_id)

    async def can_control(self, chat_id: int, user_id: Optional[int], track=None) -> bool:
        if track is not None and user_id and track.requested_by_id == user_id:
            return True
        return await self.privileged(chat_id, user_id)
