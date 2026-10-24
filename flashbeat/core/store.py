"""In-memory state with write-behind persistence.

Every read is a dict lookup (no awaiting a database on the hot path). Writes mark a
document dirty and a background task flushes them in one batch every few seconds.
Backends: MongoDB (MONGO_URL set) or a local JSON file.
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from typing import Any, Optional

from flashbeat import logger

COLLECTIONS = ("chats", "auth", "users", "blacklist", "sudo", "favs", "plays", "meta")

CHAT_DEFAULTS: dict[str, Any] = {
    "admin_only": False,     # only admins/auth users may /play
    "delete_cmds": False,    # delete command messages
    "autoplay": False,       # keep playing related songs when the queue ends
    "volume": 100,
}


class JsonBackend:
    name = "json"

    def __init__(self, path: str) -> None:
        self.path = path
        self.data: dict[str, dict[str, dict]] = {}

    async def load(self) -> dict[str, dict[str, dict]]:
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as fh:
                    self.data = json.load(fh)
            except Exception as ex:  # corrupt file: keep a copy and start clean
                logger.error("state file unreadable (%s); backing it up", ex)
                os.replace(self.path, self.path + ".bad")
                self.data = {}
        return self.data

    async def write(self, changes: list[tuple[str, str, Optional[dict]]]) -> None:
        for coll, key, doc in changes:
            bucket = self.data.setdefault(coll, {})
            if doc is None:
                bucket.pop(key, None)
            else:
                bucket[key] = doc
        snapshot = json.dumps(self.data, ensure_ascii=False)

        def _dump() -> None:
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                fh.write(snapshot)
            os.replace(tmp, self.path)

        await asyncio.to_thread(_dump)

    async def close(self) -> None:
        return None


class MongoBackend:
    name = "mongo"

    def __init__(self, url: str) -> None:
        from pymongo import AsyncMongoClient

        self.client = AsyncMongoClient(url, serverSelectionTimeoutMS=12500)
        self.db = self.client["FlashBeat"]

    async def load(self) -> dict[str, dict[str, dict]]:
        await self.client.admin.command("ping")
        out: dict[str, dict[str, dict]] = {}
        for coll in COLLECTIONS:
            docs = await self.db[coll].find({}).to_list(None)
            out[coll] = {str(d.pop("_id")): d for d in docs}
        return out

    async def write(self, changes: list[tuple[str, str, Optional[dict]]]) -> None:
        from pymongo import DeleteOne, ReplaceOne

        per_coll: dict[str, list] = {}
        for coll, key, doc in changes:
            op = DeleteOne({"_id": key}) if doc is None else ReplaceOne({"_id": key}, {"_id": key, **doc}, upsert=True)
            per_coll.setdefault(coll, []).append(op)
        await asyncio.gather(*(self.db[c].bulk_write(ops, ordered=False) for c, ops in per_coll.items()))

    async def close(self) -> None:
        await self.client.close()


class Store:
    def __init__(self, backend, flush_interval: float = 3.0) -> None:
        self.backend = backend
        self.flush_interval = flush_interval
        self.d: dict[str, dict[str, dict]] = {c: {} for c in COLLECTIONS}
        self._dirty: set[tuple[str, str]] = set()
        self._task: Optional[asyncio.Task] = None
        self._lock = asyncio.Lock()
        # hot sets for O(1) membership checks
        self.sudoers: set[int] = set()
        self.blacklist: set[int] = set()

    # ---- lifecycle ----------------------------------------------------
    async def load(self) -> None:
        loaded = await self.backend.load()
        for coll in COLLECTIONS:
            self.d[coll] = loaded.get(coll, {})
        self.sudoers = {int(k) for k in self.d["sudo"]}
        self.blacklist = {int(k) for k in self.d["blacklist"]}
        self._task = asyncio.create_task(self._flusher())
        logger.info("store ready (%s): %d chats, %d users", self.backend.name,
                    len(self.d["chats"]), len(self.d["users"]))

    async def close(self) -> None:
        if self._task:
            self._task.cancel()
        await self.flush()
        await self.backend.close()

    async def _flusher(self) -> None:
        while True:
            await asyncio.sleep(self.flush_interval)
            try:
                await self.flush()
            except Exception as ex:
                logger.error("store flush failed: %s", ex)

    async def flush(self) -> None:
        async with self._lock:
            if not self._dirty:
                return
            dirty, self._dirty = self._dirty, set()
            changes = [(c, k, self.d[c].get(k)) for c, k in dirty]
            try:
                await self.backend.write(changes)
            except Exception:
                self._dirty |= dirty   # retry next round
                raise

    def _touch(self, coll: str, key: Any) -> None:
        self._dirty.add((coll, str(key)))

    # ---- chat settings ------------------------------------------------
    def chat(self, chat_id: int) -> dict:
        doc = self.d["chats"].get(str(chat_id))
        if doc is None:
            return dict(CHAT_DEFAULTS)
        return {**CHAT_DEFAULTS, **doc}

    def setting(self, chat_id: int, key: str) -> Any:
        return self.chat(chat_id)[key]

    def set_chat(self, chat_id: int, **kv: Any) -> None:
        doc = self.d["chats"].setdefault(str(chat_id), {"t": int(time.time())})
        doc.update(kv)
        self._touch("chats", chat_id)

    def register_chat(self, chat_id: int) -> bool:
        if str(chat_id) in self.d["chats"]:
            return False
        self.d["chats"][str(chat_id)] = {"t": int(time.time())}
        self._touch("chats", chat_id)
        return True

    def register_user(self, user_id: int) -> bool:
        if str(user_id) in self.d["users"]:
            return False
        self.d["users"][str(user_id)] = {"t": int(time.time())}
        self._touch("users", user_id)
        return True

    def group_ids(self) -> list[int]:
        return [int(k) for k in self.d["chats"] if int(k) < 0]

    def user_ids(self) -> list[int]:
        return [int(k) for k in self.d["users"]]

    # ---- auth / sudo / blacklist ---------------------------------------
    def auth_users(self, chat_id: int) -> set[int]:
        return set(self.d["auth"].get(str(chat_id), {}).get("users", []))

    def is_auth(self, chat_id: int, user_id: int) -> bool:
        return user_id in self.d["auth"].get(str(chat_id), {}).get("users", ())

    def add_auth(self, chat_id: int, user_id: int) -> None:
        doc = self.d["auth"].setdefault(str(chat_id), {"users": []})
        if user_id not in doc["users"]:
            doc["users"].append(user_id)
            self._touch("auth", chat_id)

    def rm_auth(self, chat_id: int, user_id: int) -> None:
        doc = self.d["auth"].get(str(chat_id))
        if doc and user_id in doc["users"]:
            doc["users"].remove(user_id)
            self._touch("auth", chat_id)

    def add_sudo(self, user_id: int) -> None:
        self.sudoers.add(user_id)
        self.d["sudo"][str(user_id)] = {"t": int(time.time())}
        self._touch("sudo", user_id)

    def rm_sudo(self, user_id: int) -> None:
        self.sudoers.discard(user_id)
        self.d["sudo"].pop(str(user_id), None)
        self._touch("sudo", user_id)

    def ban(self, target_id: int) -> None:
        self.blacklist.add(target_id)
        self.d["blacklist"][str(target_id)] = {"t": int(time.time())}
        self._touch("blacklist", target_id)

    def unban(self, target_id: int) -> None:
        self.blacklist.discard(target_id)
        self.d["blacklist"].pop(str(target_id), None)
        self._touch("blacklist", target_id)

    # ---- favourites ---------------------------------------------------
    FAV_LIMIT = 100

    def favs(self, user_id: int) -> list[dict]:
        return list(self.d["favs"].get(str(user_id), {}).get("items", []))

    def is_fav(self, user_id: int, track_id: str) -> bool:
        return any(i["id"] == track_id for i in self.d["favs"].get(str(user_id), {}).get("items", ()))

    def toggle_fav(self, user_id: int, doc: dict) -> bool:
        """Add or remove; returns True if it is a favourite afterwards."""
        entry = self.d["favs"].setdefault(str(user_id), {"items": []})
        items = entry["items"]
        for i, it in enumerate(items):
            if it["id"] == doc["id"]:
                items.pop(i)
                self._touch("favs", user_id)
                return False
        if len(items) >= self.FAV_LIMIT:
            items.pop(0)
        items.append(doc)
        self._touch("favs", user_id)
        return True

    # ---- play history / top -------------------------------------------
    def record_play(self, chat_id: int, doc: dict) -> None:
        entry = self.d["plays"].setdefault(str(chat_id), {"recent": [], "top": {}})
        recent = entry["recent"]
        recent[:] = [r for r in recent if r["id"] != doc["id"]]
        recent.insert(0, {"id": doc["id"], "title": doc["title"], "url": doc.get("url", ""),
                          "duration": doc.get("duration", 0), "source": doc.get("source", "youtube"),
                          "channel": doc.get("channel", ""), "t": int(time.time())})
        del recent[30:]
        top = entry["top"]
        slot = top.setdefault(doc["id"], {"title": doc["title"], "n": 0, "url": doc.get("url", ""),
                                          "duration": doc.get("duration", 0),
                                          "source": doc.get("source", "youtube"),
                                          "channel": doc.get("channel", "")})
        slot["n"] += 1
        if len(top) > 400:
            for k, _ in sorted(top.items(), key=lambda kv: kv[1]["n"])[:100]:
                top.pop(k, None)
        meta = self.d["meta"].setdefault("stats", {"plays": 0, "since": int(time.time())})
        meta["plays"] += 1
        self._touch("plays", chat_id)
        self._touch("meta", "stats")

    def recent(self, chat_id: int, n: int = 10) -> list[dict]:
        return self.d["plays"].get(str(chat_id), {}).get("recent", [])[:n]

    def top(self, chat_id: int, n: int = 10) -> list[dict]:
        top = self.d["plays"].get(str(chat_id), {}).get("top", {})
        items = sorted(top.items(), key=lambda kv: kv[1]["n"], reverse=True)[:n]
        return [{"id": k, **v} for k, v in items]

    def total_plays(self) -> int:
        return self.d["meta"].get("stats", {}).get("plays", 0)
