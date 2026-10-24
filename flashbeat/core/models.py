from __future__ import annotations

import re
import time
from dataclasses import dataclass, field, replace
from typing import Optional

from flashbeat.helpers.text import fmt_time, progress_bar


@dataclass
class Track:
    id: str
    title: str
    url: str = ""
    duration: int = 0                    # seconds, 0 = unknown / live
    channel: str = ""
    thumbnail: str = ""
    views: str = ""
    source: str = "youtube"              # youtube | soundcloud | telegram | direct
    video: bool = False
    live: bool = False

    requested_by: str = ""
    requested_by_id: int = 0

    # --- filled in by the resolver -------------------------------------
    stream_url: Optional[str] = None
    headers: Optional[dict] = None
    file_path: Optional[str] = None

    # --- playback state ---------------------------------------------------
    message_id: int = 0
    via: str = ""                        # "stream" | "file" (how it is currently played)
    retries: int = 0
    started_at: float = 0.0
    paused_at: Optional[float] = None
    paused_total: float = 0.0
    offset: int = 0

    # ------------------------------------------------------------------
    @property
    def key(self) -> str:
        return f"{self.source}:{self.id}:{'v' if self.video else 'a'}"

    @property
    def safe_id(self) -> str:
        return re.sub(r"[^\w.-]", "_", self.id)[:80]

    @property
    def duration_text(self) -> str:
        return "LIVE" if self.live else (fmt_time(self.duration) if self.duration else "--:--")

    def mark_started(self, offset: int = 0) -> None:
        self.started_at = time.monotonic()
        self.offset = max(0, int(offset))
        self.paused_at = None
        self.paused_total = 0.0

    def pause(self) -> None:
        if self.paused_at is None:
            self.paused_at = time.monotonic()

    def resume(self) -> None:
        if self.paused_at is not None:
            self.paused_total += time.monotonic() - self.paused_at
            self.paused_at = None

    def elapsed(self) -> int:
        if not self.started_at:
            return 0
        ref = self.paused_at if self.paused_at is not None else time.monotonic()
        value = self.offset + ref - self.started_at - self.paused_total
        if self.duration:
            value = min(value, self.duration)
        return max(0, int(value))

    def bar(self, width: int = 14) -> str:
        return progress_bar(self.elapsed(), self.duration, width)

    def fresh_copy(self) -> "Track":
        """A copy with playback state reset (used for queue-loop)."""
        return replace(self, message_id=0, via="", retries=0, started_at=0.0,
                       paused_at=None, paused_total=0.0, offset=0)

    # --- persistence (favourites / history) ----------------------------
    def to_doc(self) -> dict:
        return {"id": self.id, "title": self.title, "url": self.url, "duration": self.duration,
                "channel": self.channel, "source": self.source, "thumbnail": self.thumbnail}

    @classmethod
    def from_doc(cls, doc: dict) -> "Track":
        return cls(id=doc["id"], title=doc.get("title", ""), url=doc.get("url", ""),
                   duration=int(doc.get("duration", 0)), channel=doc.get("channel", ""),
                   source=doc.get("source", "youtube"), thumbnail=doc.get("thumbnail", ""))
