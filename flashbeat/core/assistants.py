from __future__ import annotations

from collections import Counter

from pyrogram import Client

from flashbeat import logger
from flashbeat.config import config


class Assistants:
    """Pool of user accounts that sit in voice chats. Chats are balanced across them
    and each chat sticks to one assistant (persisted), so only one account joins a group."""

    def __init__(self, store) -> None:
        self.store = store
        self.clients: list[Client] = []

    async def boot(self) -> None:
        for i, session in enumerate(config.SESSIONS):
            client = Client(f"fb_assistant_{i + 1}", api_id=config.API_ID, api_hash=config.API_HASH,
                            session_string=session)
            await client.start()
            client.fb_index = i
            self.clients.append(client)
            me = client.me
            logger.info("assistant %d ready: %s (@%s)", i + 1, me.first_name, me.username)
            try:
                await client.send_message(config.LOGGER_ID, "FlashBeat assistant online ✅")
            except Exception:
                logger.warning("assistant %d cannot write to the log group", i + 1)

    async def stop(self) -> None:
        for c in self.clients:
            try:
                await c.stop()
            except Exception:
                pass

    def pick(self, chat_id: int) -> Client:
        saved = self.store.d["chats"].get(str(chat_id), {}).get("assistant")
        if saved is not None and saved < len(self.clients):
            return self.clients[saved]
        load = Counter(doc.get("assistant") for doc in self.store.d["chats"].values())
        idx = min(range(len(self.clients)), key=lambda i: load.get(i, 0))
        self.store.set_chat(chat_id, assistant=idx)
        return self.clients[idx]
