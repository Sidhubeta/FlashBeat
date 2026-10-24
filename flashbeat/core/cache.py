from __future__ import annotations

import asyncio
import os
import time
from collections import OrderedDict
from typing import Any, Awaitable, Callable, Hashable


class TTLCache:
    """Small LRU + TTL cache with hit/miss counters (single-threaded asyncio use)."""

    def __init__(self, maxsize: int = 256, ttl: float = 300.0, clock: Callable[[], float] = time.monotonic):
        self.maxsize, self.ttl, self._clock = maxsize, ttl, clock
        self._d: "OrderedDict[Hashable, tuple[float, Any]]" = OrderedDict()
        self.hits = 0
        self.misses = 0

    def get(self, key: Hashable, default: Any = None) -> Any:
        item = self._d.get(key)
        if item is None or item[0] < self._clock():
            if item is not None:
                del self._d[key]
            self.misses += 1
            return default
        self._d.move_to_end(key)
        self.hits += 1
        return item[1]

    def set(self, key: Hashable, value: Any, ttl: float | None = None) -> None:
        self._d[key] = (self._clock() + (self.ttl if ttl is None else ttl), value)
        self._d.move_to_end(key)
        while len(self._d) > self.maxsize:
            self._d.popitem(last=False)

    def pop(self, key: Hashable, default: Any = None) -> Any:
        item = self._d.pop(key, None)
        return default if item is None else item[1]

    def __contains__(self, key: Hashable) -> bool:
        item = self._d.get(key)
        return item is not None and item[0] >= self._clock()

    def __len__(self) -> int:
        return len(self._d)

    @property
    def hit_rate(self) -> float:
        total = self.hits + self.misses
        return self.hits / total if total else 0.0


class SingleFlight:
    """Collapse concurrent identical async jobs into one (thundering-herd guard)."""

    def __init__(self) -> None:
        self._tasks: dict[Hashable, asyncio.Future] = {}

    async def do(self, key: Hashable, factory: Callable[[], Awaitable[Any]]) -> Any:
        task = self._tasks.get(key)
        if task is None:
            task = asyncio.ensure_future(factory())
            self._tasks[key] = task
            task.add_done_callback(lambda _t, k=key: self._tasks.pop(k, None))
        return await asyncio.shield(task)

    def pending(self, key: Hashable) -> bool:
        return key in self._tasks


def dir_size(path: str) -> int:
    total = 0
    for entry in os.scandir(path):
        if entry.is_file(follow_symlinks=False):
            total += entry.stat().st_size
    return total


def prune_dir(path: str, max_bytes: int, keep: set[str] | None = None) -> int:
    """Delete least-recently-used files until the directory fits max_bytes. Returns bytes freed."""
    keep = keep or set()
    files = []
    for entry in os.scandir(path):
        if entry.is_file(follow_symlinks=False):
            st = entry.stat()
            files.append((max(st.st_atime, st.st_mtime), st.st_size, entry.path))
    total = sum(f[1] for f in files)
    freed = 0
    for _, size, p in sorted(files):
        if total <= max_bytes:
            break
        if p in keep or p.endswith((".part", ".ytdl")):
            continue
        try:
            os.remove(p)
        except OSError:
            continue
        total -= size
        freed += size
    return freed
