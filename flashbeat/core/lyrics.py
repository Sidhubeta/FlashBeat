"""Lyrics via lrclib.net (free, no API key)."""
from __future__ import annotations

import re
from typing import Optional

from flashbeat import logger

API = "https://lrclib.net/api/search"
_NOISE = re.compile(
    r"\(.*?\)|\[.*?\]|official\s*(music\s*)?(video|audio)|lyrics?(\s*video)?|full\s*(song|video)|"
    r"\b(hd|hq|4k|mv|audio|video)\b|ft\.?|feat\.?.*$", re.I)


def clean_title(title: str) -> str:
    title = _NOISE.sub(" ", title)
    title = re.sub(r"[|•·]+", " ", title)
    return " ".join(title.split())


def paginate(text: str, limit: int = 3300) -> list[str]:
    pages, cur = [], ""
    for line in text.splitlines():
        if len(cur) + len(line) + 1 > limit and cur:
            pages.append(cur.rstrip())
            cur = ""
        cur += line + "\n"
    if cur.strip():
        pages.append(cur.rstrip())
    return pages


async def fetch(session, query: str, channel: str = "") -> Optional[dict]:
    """Return {'title','artist','text'} or None."""
    q = clean_title(query)
    if not q:
        return None
    for params in ({"q": q}, {"q": f"{q} {channel}".strip()}):
        try:
            async with session.get(API, params=params, headers={"User-Agent": "FlashBeat/1.0"}) as r:
                if r.status != 200:
                    continue
                rows = await r.json(content_type=None)
        except Exception as ex:
            logger.debug("lyrics lookup failed: %s", ex)
            continue
        for row in rows or []:
            text = (row.get("plainLyrics") or "").strip()
            if text:
                return {"title": row.get("trackName") or q, "artist": row.get("artistName") or "", "text": text}
    return None
