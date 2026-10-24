"""Now-playing cover cards. Rendering runs in a worker thread and results are cached on disk."""
from __future__ import annotations

import asyncio
import colorsys
import io
import os
from pathlib import Path
from typing import Optional

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps

from flashbeat import logger
from flashbeat.core.cache import SingleFlight, prune_dir
from flashbeat.core.models import Track
from flashbeat.helpers.text import fmt_time, short

FONT_DIR = Path(__file__).resolve().parents[2] / "assets" / "fonts"
CARD_DIR = "cache/cards"
W, H = 1280, 720


def _font(name: str, size: int):
    try:
        return ImageFont.truetype(str(FONT_DIR / name), size)
    except OSError:
        return ImageFont.load_default(size)


def _accent(art: Image.Image) -> tuple[int, int, int]:
    r, g, b = art.convert("RGB").resize((1, 1), Image.Resampling.BOX).getpixel((0, 0))
    h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
    r, g, b = colorsys.hsv_to_rgb(h, max(s, 0.55), max(v, 0.9))
    return int(r * 255), int(g * 255), int(b * 255)


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, max_w: int, max_lines: int) -> list[str]:
    lines, cur = [], ""
    for word in text.split():
        trial = f"{cur} {word}".strip()
        if draw.textlength(trial, font=font) <= max_w:
            cur = trial
            continue
        if cur:
            lines.append(cur)
        cur = word
        if len(lines) == max_lines:
            break
    if cur and len(lines) < max_lines:
        lines.append(cur)
    if len(lines) == max_lines and " ".join(lines) != text:
        last = lines[-1]
        while last and draw.textlength(last + "…", font=font) > max_w:
            last = last[:-1]
        lines[-1] = last.rstrip() + "…"
    return lines or [short(text, 30)]


def draw_card(art: Image.Image, title: str, subtitle: str, duration: str) -> Image.Image:
    art = art.convert("RGB")
    accent = _accent(art)

    # blurred, darkened backdrop
    bg = ImageOps.fit(art, (W, H), Image.Resampling.BILINEAR).filter(ImageFilter.GaussianBlur(38))
    bg = ImageEnhance.Brightness(bg).enhance(0.38).convert("RGBA")
    shade = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    sd = ImageDraw.Draw(shade)
    for y in range(H):
        sd.line([(0, y), (W, y)], fill=(0, 0, 0, int(120 * y / H)))
    bg = Image.alpha_composite(bg, shade)

    # cover with shadow + rounded corners
    size, x0, y0 = 500, 90, 110
    cover = ImageOps.fit(art, (size, size), Image.Resampling.LANCZOS).convert("RGBA")
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, size, size), radius=36, fill=255)
    shadow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle((x0, y0 + 26, x0 + size, y0 + size + 26), radius=36, fill=(0, 0, 0, 170))
    bg = Image.alpha_composite(bg, shadow.filter(ImageFilter.GaussianBlur(26)))
    bg.paste(cover, (x0, y0), mask)

    d = ImageDraw.Draw(bg)
    tx, tw = 650, W - 650 - 80
    label = _font("Inter-Light.ttf", 28)
    d.text((tx, 150), "N O W   P L A Y I N G", font=label, fill=accent)

    f_title = _font("Raleway-Bold.ttf", 56)
    lines = _wrap(d, title, f_title, tw, 3)
    y = 205
    for line in lines:
        d.text((tx, y), line, font=f_title, fill=(255, 255, 255))
        y += 70
    d.text((tx, y + 14), short(subtitle, 48), font=_font("Inter-Light.ttf", 32), fill=(215, 215, 220))

    # progress bar
    by = 520
    d.rounded_rectangle((tx, by, tx + tw, by + 8), radius=4, fill=(120, 120, 135))
    d.rounded_rectangle((tx, by, tx + 26, by + 8), radius=4, fill=accent)
    d.ellipse((tx + 12, by - 8, tx + 28, by + 16), fill=(255, 255, 255))
    f_small = _font("Inter-Light.ttf", 26)
    d.text((tx, by + 28), "0:00", font=f_small, fill=(230, 230, 235))
    d.text((tx + tw - d.textlength(duration, font=f_small), by + 28), duration, font=f_small, fill=(230, 230, 235))

    d.text((tx, H - 90), "FLASHBEAT", font=_font("Raleway-Bold.ttf", 26), fill=accent)
    return bg.convert("RGB")


class Cards:
    def __init__(self, resolver) -> None:
        self.resolver = resolver
        self.flight = SingleFlight()
        os.makedirs(CARD_DIR, exist_ok=True)

    def path_for(self, track: Track) -> str:
        return os.path.join(CARD_DIR, f"{track.source}_{track.safe_id}.jpg")

    def cached(self, track: Track) -> Optional[str]:
        p = self.path_for(track)
        return p if os.path.exists(p) else None

    async def render(self, track: Track) -> Optional[str]:
        if not track.thumbnail:
            return None
        hit = self.cached(track)
        if hit:
            return hit
        try:
            return await self.flight.do(track.key, lambda: self._render(track))
        except Exception as ex:
            logger.debug("card render failed for %s: %s", track.id, ex)
            return None

    async def _render(self, track: Track) -> Optional[str]:
        async with self.resolver.session.get(track.thumbnail) as r:
            r.raise_for_status()
            raw = await r.read()
        out = self.path_for(track)
        sub = " · ".join(x for x in (track.channel, track.views and f"{track.views} views") if x)

        def work() -> str:
            art = Image.open(io.BytesIO(raw))
            card = draw_card(art, track.title, sub, track.duration_text if track.duration else "--:--")
            card.save(out, "JPEG", quality=88, optimize=True)
            return out

        path = await asyncio.to_thread(work)
        await asyncio.to_thread(prune_dir, CARD_DIR, 60 * 1024 * 1024)
        return path
