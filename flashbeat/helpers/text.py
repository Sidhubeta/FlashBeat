from __future__ import annotations

import html
import re
import secrets
from urllib.parse import parse_qs, urlparse

esc = html.escape

_URL_RE = re.compile(r"https?://[^\s<>\"']+", re.I)
_YT_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be"}
_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")


def fmt_time(seconds: int | float) -> str:
    seconds = max(0, int(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def parse_time(text: str | None) -> int:
    """'3:45' -> 225, '1:02:03' -> 3723, '90' -> 90, junk -> 0."""
    if not text:
        return 0
    text = text.strip()
    try:
        parts = [int(p) for p in text.split(":")]
    except ValueError:
        return 0
    total = 0
    for p in parts[-3:]:
        total = total * 60 + p
    return total


def fmt_size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


def human_count(n: int | float) -> str:
    n = float(n)
    for div, suffix in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if n >= div:
            return f"{n / div:.1f}".rstrip("0").rstrip(".") + suffix
    return str(int(n))


def short(text: str, limit: int) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def progress_bar(elapsed: int, total: int, width: int = 14) -> str:
    if total <= 0:
        return "━" * width
    pos = min(width - 1, int(width * min(elapsed, total) / total))
    return "━" * pos + "●" + "─" * (width - pos - 1)


def token(n: int = 5) -> str:
    return secrets.token_urlsafe(n)


def normalize_query(q: str) -> str:
    return " ".join(q.lower().split())


# --- URL helpers ------------------------------------------------------------
def find_url(text: str | None) -> str | None:
    if not text:
        return None
    m = _URL_RE.search(text)
    return m.group(0).rstrip(").,]") if m else None


def message_url(m) -> str | None:
    """First URL in a pyrogram message (text, caption, link entities), or its reply."""
    for msg in (m, getattr(m, "reply_to_message", None)):
        if not msg:
            continue
        found = find_url(msg.text or msg.caption)
        if found:
            return found
        for ent in (msg.entities or msg.caption_entities or []):
            if getattr(ent.type, "name", "") == "TEXT_LINK" and ent.url:
                return ent.url
    return None


def youtube_id(url: str) -> str | None:
    try:
        u = urlparse(url if "//" in url else "https://" + url)
    except ValueError:
        return None
    host = (u.hostname or "").lower()
    if host not in _YT_HOSTS:
        return None
    if host == "youtu.be":
        cand = u.path.lstrip("/")[:11]
    elif u.path == "/watch":
        cand = (parse_qs(u.query).get("v") or [""])[0]
    else:
        parts = [p for p in u.path.split("/") if p]
        cand = parts[1] if len(parts) >= 2 and parts[0] in ("shorts", "live", "embed", "v") else ""
    return cand if _ID_RE.match(cand) else None


def youtube_playlist_id(url: str) -> str | None:
    try:
        u = urlparse(url if "//" in url else "https://" + url)
    except ValueError:
        return None
    if (u.hostname or "").lower() not in _YT_HOSTS - {"youtu.be"}:
        return None
    lst = (parse_qs(u.query).get("list") or [""])[0]
    if not lst:
        return None
    # /playlist?list=... is always a playlist; a watch URL with v= is the single video
    if u.path == "/playlist" or "v" not in parse_qs(u.query):
        return lst
    return None


def classify_url(url: str) -> str:
    """'yt_video' | 'yt_playlist' | 'yt_other' | 'other'"""
    host = (urlparse(url).hostname or "").lower()
    if host in _YT_HOSTS:
        if youtube_playlist_id(url):
            return "yt_playlist"
        if youtube_id(url):
            return "yt_video"
        return "yt_other"
    return "other"


def parse_flags(args: list[str], known: str = "fvn") -> tuple[set[str], str]:
    """Split '-f -v some query' into ({'f','v'}, 'some query')."""
    flags: set[str] = set()
    rest: list[str] = []
    for a in args:
        if len(a) == 2 and a[0] == "-" and a[1] in known and not rest:
            flags.add(a[1])
        else:
            rest.append(a)
    return flags, " ".join(rest).strip()
