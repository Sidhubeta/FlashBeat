"""Parser for YouTube's internal search response (youtubei/v1/search).

Kept free of any network code so it is trivially testable. The structure is undocumented
and can change; the resolver races this against other providers for that reason.
"""
from __future__ import annotations

from typing import Any, Iterator

from flashbeat.helpers.text import human_count, parse_time

SEARCH_URL = "https://www.youtube.com/youtubei/v1/search?prettyPrint=false"
CLIENT_VERSION = "2.20250620.01.00"
VIDEOS_ONLY = "EgIQAQ%3D%3D"      # search filter: type = video


def search_payload(query: str) -> dict:
    return {
        "context": {"client": {"clientName": "WEB", "clientVersion": CLIENT_VERSION, "hl": "en", "gl": "US"}},
        "query": query,
        "params": VIDEOS_ONLY,
    }


def _text(node: Any) -> str:
    if not isinstance(node, dict):
        return ""
    if "simpleText" in node:
        return node["simpleText"]
    return "".join(r.get("text", "") for r in node.get("runs", []))


def _renderers(node: Any) -> Iterator[dict]:
    if isinstance(node, dict):
        if "videoRenderer" in node:
            yield node["videoRenderer"]
            return
        for value in node.values():
            yield from _renderers(value)
    elif isinstance(node, list):
        for value in node:
            yield from _renderers(value)


def _views(text: str) -> str:
    digits = "".join(ch for ch in text if ch.isdigit())
    return human_count(int(digits)) if digits else ""


def parse_search(data: dict, limit: int = 5) -> list[dict]:
    out: list[dict] = []
    for vr in _renderers(data):
        vid = vr.get("videoId")
        length = _text(vr.get("lengthText"))
        if not vid or not length:          # no length -> live stream or premiere: skip
            continue
        out.append({
            "id": vid,
            "title": _text(vr.get("title")) or "Unknown",
            "duration": parse_time(length),
            "channel": _text(vr.get("ownerText")) or _text(vr.get("longBylineText")),
            "views": _views(_text(vr.get("viewCountText"))),
        })
        if len(out) >= limit:
            break
    return out
