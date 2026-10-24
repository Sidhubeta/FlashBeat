from __future__ import annotations

import random
from collections import deque
from typing import Optional

from flashbeat.core.models import Track

LOOP_OFF, LOOP_TRACK, LOOP_QUEUE = 0, 1, 2
LOOP_NAMES = {LOOP_OFF: "off", LOOP_TRACK: "track", LOOP_QUEUE: "queue"}


class ChatQueue:
    """current track + FIFO of upcoming tracks + short play history."""

    def __init__(self) -> None:
        self.current: Optional[Track] = None
        self.upcoming: deque[Track] = deque()
        self.history: deque[str] = deque(maxlen=60)   # track ids, for autoplay de-duplication
        self.last: Optional[Track] = None             # last finished track (autoplay seed)
        self.loop = LOOP_OFF

    # ---- inspection -------------------------------------------------
    def __len__(self) -> int:
        return len(self.upcoming) + (1 if self.current else 0)

    def all(self) -> list[Track]:
        return ([self.current] if self.current else []) + list(self.upcoming)

    def find(self, track_id: str) -> tuple[int, Optional[Track]]:
        for i, t in enumerate(self.all()):
            if t.id == track_id:
                return i, t
        return -1, None

    def peek_next(self) -> Optional[Track]:
        if self.upcoming:
            return self.upcoming[0]
        if self.loop == LOOP_QUEUE and self.current:
            return self.current
        return None

    # ---- mutation ---------------------------------------------------
    def add(self, track: Track) -> int:
        """Append. Returns position: 0 = plays now, n = n-th in line."""
        if self.current is None:
            self.current = track
            return 0
        self.upcoming.append(track)
        return len(self.upcoming)

    def add_next(self, track: Track) -> int:
        if self.current is None:
            self.current = track
            return 0
        self.upcoming.appendleft(track)
        return 1

    def force(self, track: Track) -> None:
        """Replace the current track and play it immediately."""
        if self.current:
            self._finish(self.current)
        self.current = track

    def advance(self, skip: bool = False) -> Optional[Track]:
        """Finish the current track and return the new current (or None when the queue is empty)."""
        cur = self.current
        if cur is not None:
            if self.loop == LOOP_TRACK and not skip:
                return self._restart(cur)
            self._finish(cur)
            if self.loop == LOOP_QUEUE:
                self.upcoming.append(cur.fresh_copy())
        self.current = self.upcoming.popleft() if self.upcoming else None
        return self.current

    def _restart(self, cur: Track) -> Track:
        fresh = cur.fresh_copy()
        self.current = fresh
        return fresh

    def _finish(self, t: Track) -> None:
        self.history.append(t.id)
        self.last = t

    def shuffle(self) -> int:
        items = list(self.upcoming)
        random.shuffle(items)
        self.upcoming = deque(items)
        return len(items)

    def remove(self, pos: int) -> Optional[Track]:
        """Remove the pos-th upcoming track (1-based)."""
        if not 1 <= pos <= len(self.upcoming):
            return None
        items = list(self.upcoming)
        track = items.pop(pos - 1)
        self.upcoming = deque(items)
        return track

    def move(self, src: int, dst: int) -> Optional[Track]:
        n = len(self.upcoming)
        if not (1 <= src <= n and 1 <= dst <= n):
            return None
        items = list(self.upcoming)
        track = items.pop(src - 1)
        items.insert(dst - 1, track)
        self.upcoming = deque(items)
        return track

    def clear_upcoming(self) -> int:
        n = len(self.upcoming)
        self.upcoming.clear()
        return n

    def reset(self) -> None:
        self.current = None
        self.upcoming.clear()
        self.loop = LOOP_OFF


class QueueManager:
    def __init__(self) -> None:
        self._queues: dict[int, ChatQueue] = {}

    def get(self, chat_id: int) -> ChatQueue:
        q = self._queues.get(chat_id)
        if q is None:
            q = self._queues[chat_id] = ChatQueue()
        return q

    def peek(self, chat_id: int) -> Optional[ChatQueue]:
        return self._queues.get(chat_id)

    def drop(self, chat_id: int) -> None:
        self._queues.pop(chat_id, None)

    def active(self) -> list[int]:
        return [cid for cid, q in self._queues.items() if q.current]
