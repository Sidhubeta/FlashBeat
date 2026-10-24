"""Song discovery + stream resolution - the part that decides how fast playback starts.

Speed techniques used here
  * search providers are *raced* (YouTube's internal API, py-yt-search) and a slower
    yt-dlp / SoundCloud search is hedged in only if nothing answered within 1.5 s
  * results are cached (search 15 min, stream URLs until their own `expire` stamp)
  * identical concurrent jobs are collapsed (SingleFlight)
  * we resolve a *direct stream URL* instead of downloading the whole file first
  * the next queue item is resolved while the current one is playing (prefetch)
  * a warm-up at boot opens the TLS connection and primes yt-dlp's JS solver
"""
from __future__ import annotations

import asyncio
import glob
import hashlib
import os
import random
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from typing import Awaitable, Callable, Optional
from urllib.parse import parse_qs, urlparse

from flashbeat import logger
from flashbeat.config import config
from flashbeat.core import innertube
from flashbeat.core.cache import SingleFlight, TTLCache, prune_dir
from flashbeat.core.models import Track
from flashbeat.helpers import text as T

try:                                   # optional speedup
    import orjson as _json

    _loads = _json.loads
except ImportError:                    # pragma: no cover
    import json as _json

    _loads = _json.loads

DOWNLOAD_DIR = "downloads"
COOKIE_DIR = "cookies"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

AUDIO_FMT = "bestaudio[acodec=opus]/bestaudio[ext=webm]/bestaudio/best"
VIDEO_STREAM_FMT = "best[height<=720]/best"                       # single muxed URL
VIDEO_FILE_FMT = "bestvideo[height<=720]+bestaudio/best[height<=720]/best"
MEDIA_EXT = (".m3u8", ".mp3", ".aac", ".ogg", ".opus", ".flac", ".wav", ".m4a", ".mp4", ".webm", ".mkv")


class ResolveError(Exception):
    pass


class _Silent:
    def debug(self, *_): ...
    def info(self, *_): ...
    def warning(self, *_): ...
    def error(self, *_): ...


class Metrics:
    def __init__(self) -> None:
        self.samples = {k: deque(maxlen=50) for k in ("search", "resolve", "download")}

    def add(self, kind: str, ms: float) -> None:
        self.samples[kind].append(ms)

    def avg(self, kind: str) -> Optional[float]:
        s = self.samples[kind]
        return sum(s) / len(s) if s else None


def _ttl_from_url(url: str) -> float:
    try:
        expire = int(parse_qs(urlparse(url).query).get("expire", ["0"])[0])
    except ValueError:
        expire = 0
    if expire:
        return max(60.0, min(6 * 3600.0, expire - time.time() - 300))
    return 3600.0


class Resolver:
    def __init__(self) -> None:
        self.session = None
        self.pool = ThreadPoolExecutor(max_workers=config.YTDLP_WORKERS, thread_name_prefix="ytdlp")
        self.sem = asyncio.Semaphore(config.YTDLP_WORKERS)
        self.search_cache = TTLCache(512, ttl=900)
        self.stream_cache = TTLCache(512, ttl=3600)
        self.flight = SingleFlight()
        self.metrics = Metrics()
        self.protect: set[str] = set()          # files that must survive cache pruning
        self._bg: set[asyncio.Task] = set()
        self._cookie_files: list[str] = []

    # ------------------------------------------------------------------ lifecycle
    async def start(self) -> None:
        import aiohttp

        os.makedirs(DOWNLOAD_DIR, exist_ok=True)
        os.makedirs(COOKIE_DIR, exist_ok=True)
        self.session = aiohttp.ClientSession(
            connector=aiohttp.TCPConnector(limit=64, ttl_dns_cache=300),
            timeout=aiohttp.ClientTimeout(total=15),
            headers={"User-Agent": UA},
        )
        if config.COOKIES_URL:
            await self._fetch_cookies()
        self._cookie_files = glob.glob(os.path.join(COOKIE_DIR, "*.txt"))
        if not self._cookie_files:
            logger.warning("No cookies found in %s/ - YouTube may block some requests.", COOKIE_DIR)
        self._spawn(self._warm())

    async def close(self) -> None:
        for t in list(self._bg):
            t.cancel()
        if self.session:
            await self.session.close()
        self.pool.shutdown(wait=False, cancel_futures=True)

    def _spawn(self, coro: Awaitable) -> asyncio.Task:
        task = asyncio.ensure_future(coro)
        self._bg.add(task)
        task.add_done_callback(self._bg.discard)
        return task

    async def _fetch_cookies(self) -> None:
        for i, url in enumerate(config.COOKIES_URL):
            try:
                async with self.session.get(url) as r:
                    r.raise_for_status()
                    with open(os.path.join(COOKIE_DIR, f"remote_{i}.txt"), "wb") as fh:
                        fh.write(await r.read())
            except Exception as ex:
                logger.warning("cookie download failed (%s): %s", url.split("/")[2], ex)

    async def _warm(self) -> None:
        t0 = time.perf_counter()
        try:
            await self._search_innertube("music", 1)                       # DNS + TLS + HTTP/1.1 keep-alive
            await self._resolve_impl(Track(id="jNQXAC9IVRw", title="warmup",
                                           url="https://www.youtube.com/watch?v=jNQXAC9IVRw"))
            logger.info("resolver warm-up finished in %.1fs", time.perf_counter() - t0)
        except Exception as ex:
            logger.info("resolver warm-up skipped: %s", ex)

    # ------------------------------------------------------------------ yt-dlp plumbing
    def _opts(self, *, video: bool, download: bool = False, flat: bool = False,
              cookie: Optional[str] = None, outtmpl: Optional[str] = None,
              playlist_end: Optional[int] = None) -> dict:
        o: dict = {
            "quiet": True, "no_warnings": True, "logger": _Silent(),
            "noplaylist": not flat, "skip_download": not download,
            "nocheckcertificate": True, "geo_bypass": True,
            "socket_timeout": 12, "retries": 2, "extractor_retries": 2,
            "remote_components": ["ejs:github"],
        }
        if cookie:
            o["cookiefile"] = cookie
        if config.YT_PLAYER_CLIENTS:
            o["extractor_args"] = {"youtube": {"player_client": config.YT_PLAYER_CLIENTS}}
        if flat:
            o.update(extract_flat="in_playlist", lazy_playlist=True)
            if playlist_end:
                o["playlistend"] = playlist_end
        if download:
            o.update(format=VIDEO_FILE_FMT if video else AUDIO_FMT, outtmpl=outtmpl,
                     overwrites=False, concurrent_fragment_downloads=4)
            if video:
                o["merge_output_format"] = "mp4"
        else:
            o["format"] = VIDEO_STREAM_FMT if video else AUDIO_FMT
        return o

    async def _extract(self, target: str, *, video: bool = False, download: bool = False,
                       flat: bool = False, outtmpl: Optional[str] = None,
                       playlist_end: Optional[int] = None, timeout: Optional[float] = None) -> dict:
        import yt_dlp

        cookies: list[Optional[str]] = random.sample(self._cookie_files, min(2, len(self._cookie_files)))
        cookies.append(None)
        last: Optional[Exception] = None
        loop = asyncio.get_running_loop()
        for cookie in cookies:
            opts = self._opts(video=video, download=download, flat=flat, cookie=cookie,
                              outtmpl=outtmpl, playlist_end=playlist_end)

            def run(opts=opts):
                with yt_dlp.YoutubeDL(opts) as ydl:
                    return ydl.extract_info(target, download=download)

            try:
                async with self.sem:
                    info = await asyncio.wait_for(loop.run_in_executor(self.pool, run),
                                                  timeout or config.RESOLVE_TIMEOUT)
                if info:
                    return info
            except asyncio.TimeoutError as ex:
                last = ex
                break
            except Exception as ex:
                last = ex
        raise ResolveError(f"{type(last).__name__}: {last}"[:300] if last else "no result")

    # ------------------------------------------------------------------ search
    def _yt_track(self, vid: str, title: str, duration: int, channel: str, views: str = "") -> Track:
        return Track(id=vid, title=title, url=f"https://www.youtube.com/watch?v={vid}", duration=duration,
                     channel=channel, views=views, source="youtube",
                     thumbnail=f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg")

    async def _search_innertube(self, query: str, limit: int) -> list[Track]:
        async with self.session.post(
            innertube.SEARCH_URL, json=innertube.search_payload(query),
            headers={"Origin": "https://www.youtube.com", "X-YouTube-Client-Name": "1",
                     "X-YouTube-Client-Version": innertube.CLIENT_VERSION},
        ) as r:
            r.raise_for_status()
            data = _loads(await r.read())
        return [self._yt_track(x["id"], x["title"], x["duration"], x["channel"], x["views"])
                for x in innertube.parse_search(data, limit)]

    async def _search_pyyt(self, query: str, limit: int) -> list[Track]:
        try:
            from py_yt import VideosSearch
        except ImportError:
            return []
        res = await VideosSearch(query, limit=limit, with_live=False).next()
        out = []
        for d in (res or {}).get("result", []):
            if not d.get("duration"):
                continue
            out.append(self._yt_track(d["id"], d.get("title") or "Unknown", T.parse_time(d["duration"]),
                                      (d.get("channel") or {}).get("name", ""),
                                      (d.get("viewCount") or {}).get("short", "")))
        return out[:limit]

    async def _search_ytdlp(self, prefix: str, query: str, limit: int) -> list[Track]:
        info = await self._extract(f"{prefix}{limit}:{query}", flat=True, timeout=20)
        out = []
        for e in info.get("entries") or []:
            if not e or not e.get("id"):
                continue
            if prefix == "ytsearch":
                out.append(self._yt_track(e["id"], e.get("title") or "Unknown", int(e.get("duration") or 0),
                                          e.get("channel") or e.get("uploader") or "",
                                          T.human_count(e["view_count"]) if e.get("view_count") else ""))
            else:
                out.append(Track(id=str(e["id"]), title=e.get("title") or "Unknown", url=e.get("url") or "",
                                 duration=int(e.get("duration") or 0), channel=e.get("uploader") or "",
                                 source="soundcloud", thumbnail=(e.get("thumbnails") or [{}])[-1].get("url", "")))
        return out[:limit]

    async def _hedged(self, primary: list[Callable[[], Awaitable[list]]],
                      hedge: Optional[Callable[[], Awaitable[list]]], hedge_after: float = 1.5,
                      timeout: float = 20.0) -> list:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        tasks = {asyncio.ensure_future(f()) for f in primary}
        hedged = hedge is None
        try:
            while True:
                if not tasks and hedged:
                    return []
                wait = deadline - loop.time() if hedged else hedge_after
                if wait <= 0:
                    return []
                done: set = set()
                if tasks:
                    done, _ = await asyncio.wait(tasks, timeout=wait, return_when=asyncio.FIRST_COMPLETED)
                for d in done:
                    tasks.discard(d)
                    try:
                        res = d.result()
                    except Exception:
                        res = None
                    if res:
                        return res
                if not hedged:
                    tasks.add(asyncio.ensure_future(hedge()))
                    hedged = True
        finally:
            for t in tasks:
                t.cancel()

    async def search(self, query: str, limit: int = 1, video: bool = False) -> list[Track]:
        key = (T.normalize_query(query), limit)
        hit = self.search_cache.get(key)
        if hit is None:
            t0 = time.perf_counter()
            hit = await self._hedged(
                [lambda: self._search_innertube(query, limit), lambda: self._search_pyyt(query, limit)],
                lambda: self._search_ytdlp("ytsearch", query, limit),
            )
            if not hit:   # last resort: SoundCloud keeps the bot useful during YouTube blocks
                try:
                    hit = await self._search_ytdlp("scsearch", query, limit)
                except ResolveError:
                    hit = []
            self.metrics.add("search", (time.perf_counter() - t0) * 1000)
            if hit:
                self.search_cache.set(key, hit)
        return [replace(t, video=video) for t in hit]     # copies: queue state must not be shared

    # ------------------------------------------------------------------ URLs
    def _track_from_info(self, info: dict, video: bool) -> Track:
        extractor = (info.get("extractor_key") or "").lower()
        source = "youtube" if extractor.startswith("youtube") else ("soundcloud" if "soundcloud" in extractor else "direct")
        vid = str(info.get("id"))
        thumb = info.get("thumbnail") or ""
        if source == "youtube":
            thumb = f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg"
        views = T.human_count(info["view_count"]) if info.get("view_count") else ""
        return Track(id=vid, title=info.get("title") or "Unknown",
                     url=info.get("webpage_url") or info.get("original_url") or "",
                     duration=int(info.get("duration") or 0), channel=info.get("channel") or info.get("uploader") or "",
                     thumbnail=thumb, views=views, source=source, video=video, live=bool(info.get("is_live")))

    async def from_url(self, url: str, video: bool = False) -> list[Track]:
        kind = T.classify_url(url)
        if kind == "yt_playlist":
            return await self.playlist(url, video)
        try:
            info = await self._extract(url, video=video)
        except ResolveError:
            direct = await self._direct_track(url, video)
            if direct:
                return [direct]
            raise
        if info.get("entries"):
            info = next((e for e in info["entries"] if e), info)
        track = self._track_from_info(info, video)
        if info.get("url"):                                  # extraction already gave us the stream - keep it
            self._cache_stream(track, info["url"], info.get("http_headers"))
        return [track]

    async def playlist(self, url: str, video: bool = False) -> list[Track]:
        info = await self._extract(url, flat=True, playlist_end=config.PLAYLIST_LIMIT)
        out = []
        for e in info.get("entries") or []:
            if not e or not e.get("id") or not e.get("duration"):
                continue
            out.append(self._yt_track(e["id"], e.get("title") or "Unknown", int(e["duration"]),
                                      e.get("channel") or e.get("uploader") or "") if len(str(e["id"])) == 11
                       else Track(id=str(e["id"]), title=e.get("title") or "Unknown", url=e.get("url") or "",
                                  duration=int(e["duration"]), channel=e.get("uploader") or "", source="direct"))
        return [replace(t, video=video) for t in out[: config.PLAYLIST_LIMIT]]

    async def _direct_track(self, url: str, video: bool) -> Optional[Track]:
        """Plain media / radio / HLS URL that yt-dlp has no extractor for."""
        path = urlparse(url).path.lower()
        ok = path.endswith(MEDIA_EXT)
        if not ok:
            try:
                async with self.session.head(url, allow_redirects=True, timeout=5) as r:
                    ctype = r.headers.get("Content-Type", "").lower()
                    ok = ctype.startswith(("audio/", "video/")) or "mpegurl" in ctype
            except Exception:
                ok = False
        if not ok:
            return None
        live = path.endswith(".m3u8")
        name = os.path.basename(path) or urlparse(url).hostname or "Stream"
        return Track(id="d" + hashlib.sha1(url.encode()).hexdigest()[:10], title=name[:60], url=url, source="direct", video=video,
                     live=live, stream_url=url, channel=urlparse(url).hostname or "")

    # ------------------------------------------------------------------ stream resolution
    def _cache_stream(self, track: Track, url: str, headers: Optional[dict]) -> None:
        self.stream_cache.set(track.key, (url, headers), ttl=_ttl_from_url(url))

    async def _resolve_impl(self, track: Track) -> tuple[str, Optional[dict]]:
        t0 = time.perf_counter()
        target = track.url or f"https://www.youtube.com/watch?v={track.id}"
        info = await self._extract(target, video=track.video)
        if info.get("entries"):
            info = next((e for e in info["entries"] if e), info)
        url = info.get("url")
        if not url:
            raise ResolveError("no direct stream URL for this item")
        headers = info.get("http_headers")
        self._cache_stream(track, url, headers)
        if not track.duration and info.get("duration"):
            track.duration = int(info["duration"])
        self.metrics.add("resolve", (time.perf_counter() - t0) * 1000)
        return url, headers

    def cached_file(self, track: Track) -> Optional[str]:
        for p in glob.glob(os.path.join(DOWNLOAD_DIR, glob.escape(track.safe_id) + ".*")):
            name = os.path.basename(p)
            if name.endswith((".part", ".ytdl", ".temp")):
                continue
            if name.startswith(track.safe_id + ".video.") != track.video:
                continue
            try:
                os.utime(p)                                   # mark as recently used for LRU pruning
            except OSError:
                pass
            return p
        return None

    async def resolve(self, track: Track, fresh: bool = False) -> Track:
        """Make `track` playable: fills file_path or stream_url. Raises ResolveError."""
        if track.source == "telegram" or (track.file_path and os.path.exists(track.file_path)):
            return track
        if track.source == "direct" and track.stream_url:
            return track
        if fresh:
            self.stream_cache.pop(track.key)
            track.stream_url = None
        else:
            hit = self.stream_cache.get(track.key)
            if hit:
                track.stream_url, track.headers = hit
                return track
            path = self.cached_file(track)
            if path:
                track.file_path = path
                return track
        url, headers = await self.flight.do(("resolve", track.key, fresh), lambda: self._resolve_impl(track))
        track.stream_url, track.headers = url, headers
        return track

    def prefetch(self, track: Optional[Track]) -> None:
        """Fire-and-forget resolve so the next song starts instantly."""
        if not config.PREFETCH or track is None:
            return

        async def job() -> None:
            try:
                await self.resolve(track)
            except Exception as ex:
                logger.debug("prefetch failed for %s: %s", track.id, ex)

        self._spawn(job())

    # ------------------------------------------------------------------ downloads (fallback + cache)
    async def download(self, track: Track) -> Optional[str]:
        path = self.cached_file(track)
        if path:
            track.file_path = path
            return path
        path = await self.flight.do(("dl", track.key), lambda: self._download_impl(track))
        if path:
            track.file_path = path
        return path

    async def _download_impl(self, track: Track) -> Optional[str]:
        t0 = time.perf_counter()
        outtmpl = os.path.join(DOWNLOAD_DIR, f"{track.safe_id}{'.video' if track.video else ''}.%(ext)s")
        target = track.url or f"https://www.youtube.com/watch?v={track.id}"
        try:
            info = await self._extract(target, video=track.video, download=True, outtmpl=outtmpl,
                                       timeout=config.DOWNLOAD_TIMEOUT)
        except ResolveError as ex:
            logger.warning("download failed for %s: %s", track.id, ex)
            return None
        path = ((info.get("requested_downloads") or [{}])[0]).get("filepath") or info.get("filepath")
        if not path or not os.path.exists(path):
            return None
        self.metrics.add("download", (time.perf_counter() - t0) * 1000)
        limit = config.CACHE_MAX_MB * 1024 * 1024
        await asyncio.to_thread(prune_dir, DOWNLOAD_DIR, limit, set(self.protect))
        return path

    def warm_download(self, track: Track) -> None:
        """Background file copy so replays / loops / seeks are instant and stream-expiry-proof."""
        if not config.CACHE_DOWNLOADS or track.source in ("telegram", "direct") or track.live:
            return
        if track.duration > 20 * 60:           # don't pull huge files for a convenience cache
            return

        async def job() -> None:
            await self.download(track)

        self._spawn(job())

    # ------------------------------------------------------------------ related (autoplay)
    async def related(self, seed: Track, exclude: set[str], limit: int = 1) -> list[Track]:
        out: list[Track] = []
        if seed.source == "youtube":
            try:
                info = await self._extract(f"https://www.youtube.com/watch?v={seed.id}&list=RD{seed.id}",
                                           flat=True, playlist_end=25, timeout=20)
                for e in info.get("entries") or []:
                    if not e or not e.get("id") or e["id"] in exclude or e["id"] == seed.id:
                        continue
                    dur = int(e.get("duration") or 0)
                    if dur and not 60 <= dur <= 900:
                        continue
                    out.append(self._yt_track(e["id"], e.get("title") or "Unknown", dur,
                                              e.get("channel") or e.get("uploader") or ""))
                    if len(out) >= limit:
                        return out
            except ResolveError as ex:
                logger.info("mix lookup failed: %s", ex)
        for t in await self.search(f"{seed.title} {seed.channel}".strip(), limit + 4):
            if t.id not in exclude and t.id != seed.id:
                out.append(t)
                if len(out) >= limit:
                    break
        return out[:limit]

    # ------------------------------------------------------------------ stats
    def stats(self) -> dict:
        return {
            "search_ms": self.metrics.avg("search"), "resolve_ms": self.metrics.avg("resolve"),
            "download_ms": self.metrics.avg("download"),
            "search_hit": self.search_cache.hit_rate, "stream_hit": self.stream_cache.hit_rate,
            "search_cached": len(self.search_cache), "streams_cached": len(self.stream_cache),
        }
