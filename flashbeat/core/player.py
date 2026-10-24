"""Playback orchestration: queue <-> voice engine <-> resolver <-> announcer.

The engine and announcer are injected, so this module has no Telegram imports and the
state machine can be exercised with fakes (see tests/test_player.py).

Resilience features
  * stream-first: a failed stream falls back to a downloaded file automatically
  * self-healing: a stream that dies mid-track is re-resolved (then downloaded) and
    resumed at the exact second it stopped
  * autoplay: the next related song is looked up while the current one is playing
  * one lock per chat so /skip, a stream-end event and autoplay can never race
"""
from __future__ import annotations

import asyncio
import os
from collections import defaultdict
from typing import Optional

from flashbeat import logger
from flashbeat.config import config
from flashbeat.core.errors import NoVoiceChat, StreamFailed, TelegramDown
from flashbeat.core.models import Track
from flashbeat.core.queue import LOOP_NAMES, QueueManager
from flashbeat.core.resolver import ResolveError


class Player:
    def __init__(self, engine, queue: QueueManager, store, resolver, announcer) -> None:
        self.engine, self.queue, self.store = engine, queue, store
        self.resolver, self.announcer = resolver, announcer
        self.calls: dict[int, bool] = {}              # chat_id -> paused?
        self._locks: dict[int, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._idle: dict[int, asyncio.Task] = {}
        self._bg: set[asyncio.Task] = set()
        engine.on_stream_end = self.on_stream_end
        engine.on_chat_closed = self.stop

    # ------------------------------------------------------------------ helpers
    def _spawn(self, coro) -> asyncio.Task:
        t = asyncio.ensure_future(coro)
        self._bg.add(t)
        t.add_done_callback(self._bg.discard)
        return t

    def lock(self, chat_id: int) -> asyncio.Lock:
        return self._locks[chat_id]

    def is_active(self, chat_id: int) -> bool:
        return chat_id in self.calls

    def is_paused(self, chat_id: int) -> bool:
        return self.calls.get(chat_id, False)

    def current(self, chat_id: int) -> Optional[Track]:
        q = self.queue.peek(chat_id)
        return q.current if q else None

    def _cancel_idle(self, chat_id: int) -> None:
        t = self._idle.pop(chat_id, None)
        if t:
            t.cancel()

    # ------------------------------------------------------------------ queueing
    def enqueue(self, chat_id: int, track: Track, mode: str = "end") -> int:
        """mode: 'end' | 'next' | 'force'. Returns position (0 = starts now)."""
        q = self.queue.get(chat_id)
        if mode == "force":
            q.force(track)
            return 0
        return q.add_next(track) if mode == "next" else q.add(track)

    # ------------------------------------------------------------------ core play loop
    async def start(self, chat_id: int, status=None, seek: int = 0) -> bool:
        async with self.lock(chat_id):
            return await self._run_current(chat_id, status, seek)

    async def _prepare(self, track: Track) -> None:
        """Make the track playable: stream URL if we can, otherwise a downloaded file."""
        if track.file_path and os.path.exists(track.file_path):
            return
        try:
            await self.resolver.resolve(track)
        except ResolveError:
            if not await self.resolver.download(track):
                raise

    async def _play(self, chat_id: int, track: Track, seek: int = 0) -> None:
        await self._prepare(track)
        try:
            await self.engine.play(chat_id, track, seek)
        except StreamFailed as ex:
            if track.via == "file" or track.source == "telegram":
                raise
            logger.info("stream failed for %s (%s) - falling back to file", track.id, ex)
            if not await self.resolver.download(track):
                raise
            await self.engine.play(chat_id, track, seek)
        track.mark_started(seek)

    async def _run_current(self, chat_id: int, status=None, seek: int = 0) -> bool:
        q = self.queue.get(chat_id)
        self._cancel_idle(chat_id)
        failures = 0
        while True:
            track = q.current
            if track is None:
                if await self._autoplay_fill(chat_id):
                    continue
                await self._finish(chat_id)
                return False
            try:
                await self._play(chat_id, track, seek)
            except NoVoiceChat:
                await self._teardown(chat_id)
                await self.announcer.notice(chat_id, "no_vc")
                return False
            except TelegramDown:
                await self._teardown(chat_id)
                await self.announcer.notice(chat_id, "tg_down")
                return False
            except (StreamFailed, ResolveError) as ex:
                failures += 1
                logger.warning("cannot play %s: %s", track.id, ex)
                await self.announcer.failed(chat_id, track, status)
                status = None
                if failures >= 4:
                    await self._teardown(chat_id)
                    return False
                q.advance(skip=True)
                seek = 0
                continue
            self.calls[chat_id] = False
            if seek == 0 and track.retries == 0:
                self.store.record_play(chat_id, track.to_doc())
            self._spawn(self.announcer.now_playing(chat_id, track, status))
            self._after_start(chat_id, track)
            return True

    def _after_start(self, chat_id: int, track: Track) -> None:
        q = self.queue.get(chat_id)
        nxt = q.peek_next()
        self.resolver.prefetch(nxt)
        self.resolver.protect = {p for p in (track.file_path, nxt.file_path if nxt else None) if p}
        self.resolver.warm_download(track)
        if self.store.setting(chat_id, "autoplay") and not q.upcoming:
            self._spawn(self._prefill(chat_id, track))

    # ------------------------------------------------------------------ autoplay
    async def _related(self, chat_id: int, seed: Track) -> Optional[Track]:
        q = self.queue.get(chat_id)
        try:
            found = await self.resolver.related(seed, set(q.history) | {t.id for t in q.all()}, 1)
        except Exception as ex:
            logger.info("autoplay lookup failed: %s", ex)
            return None
        if not found:
            return None
        t = found[0]
        t.requested_by, t.requested_by_id, t.video = "Autoplay", 0, seed.video
        return t

    async def _prefill(self, chat_id: int, seed: Track) -> None:
        t = await self._related(chat_id, seed)
        q = self.queue.get(chat_id)
        if t and q.current is seed and not q.upcoming:
            q.add(t)
            self.resolver.prefetch(t)

    async def _autoplay_fill(self, chat_id: int) -> bool:
        q = self.queue.get(chat_id)
        if not (self.store.setting(chat_id, "autoplay") and q.last):
            return False
        t = await self._related(chat_id, q.last)
        if not t:
            return False
        q.add(t)
        return True

    # ------------------------------------------------------------------ end of stream / self-healing
    @staticmethod
    def _premature(track: Track) -> bool:
        return (track.via == "stream" and not track.live and track.duration >= 20
                and track.elapsed() < track.duration - 8)

    async def _self_heal(self, chat_id: int, track: Track) -> bool:
        if not self._premature(track) or track.retries >= 2:
            return False
        track.retries += 1
        pos = track.elapsed()
        logger.info("stream of %s ended early at %ss (retry %d) - healing", track.id, pos, track.retries)
        try:
            if track.retries == 1:
                await self.resolver.resolve(track, fresh=True)
            elif not await self.resolver.download(track):
                return False
            await self.engine.play(chat_id, track, pos)
            track.mark_started(pos)
            return True
        except Exception as ex:
            logger.info("heal failed: %s", ex)
            return False

    async def on_stream_end(self, chat_id: int) -> None:
        async with self.lock(chat_id):
            track = self.current(chat_id)
            if track is None or chat_id not in self.calls:
                return
            if await self._self_heal(chat_id, track):
                return
            await self.announcer.finished(chat_id, track)
            self.queue.get(chat_id).advance()
            await self._run_current(chat_id)

    # ------------------------------------------------------------------ controls
    async def skip(self, chat_id: int) -> Optional[Track]:
        async with self.lock(chat_id):
            q = self.queue.peek(chat_id)
            if not q or not q.current:
                return None
            await self.announcer.finished(chat_id, q.current)
            q.advance(skip=True)
            return q.current if await self._run_current(chat_id) else None

    async def pause(self, chat_id: int) -> bool:
        async with self.lock(chat_id):
            track = self.current(chat_id)
            if not track or self.calls.get(chat_id) is not False:
                return False
            try:
                await self.engine.pause(chat_id)
            except NoVoiceChat:
                await self._teardown(chat_id)
                return False
            track.pause()
            self.calls[chat_id] = True
        await self.announcer.refresh(chat_id)
        return True

    async def resume(self, chat_id: int) -> bool:
        async with self.lock(chat_id):
            track = self.current(chat_id)
            if not track or self.calls.get(chat_id) is not True:
                return False
            try:
                await self.engine.resume(chat_id)
            except NoVoiceChat:
                await self._teardown(chat_id)
                return False
            track.resume()
            self.calls[chat_id] = False
        await self.announcer.refresh(chat_id)
        return True

    async def stop(self, chat_id: int) -> None:
        async with self.lock(chat_id):
            await self._teardown(chat_id)

    async def seek(self, chat_id: int, *, delta: Optional[int] = None, to: Optional[int] = None) -> Optional[int]:
        """Returns the new position, -1 if beyond the end, None if nothing seekable is playing."""
        async with self.lock(chat_id):
            track = self.current(chat_id)
            if not track or track.live or chat_id not in self.calls:
                return None
            pos = max(0, to if to is not None else track.elapsed() + (delta or 0))
            if track.duration and pos >= track.duration - 3:
                return -1
            await self._play(chat_id, track, pos)
            self.calls[chat_id] = False
            return pos

    async def set_loop(self, chat_id: int, mode: int) -> str:
        self.queue.get(chat_id).loop = mode
        await self.announcer.refresh(chat_id)
        return LOOP_NAMES[mode]

    async def set_volume(self, chat_id: int, volume: int) -> bool:
        ok = await self.engine.set_volume(chat_id, volume)
        if ok:
            self.store.set_chat(chat_id, volume=volume)
        return ok

    async def toggle_autoplay(self, chat_id: int) -> bool:
        value = not self.store.setting(chat_id, "autoplay")
        self.store.set_chat(chat_id, autoplay=value)
        track = self.current(chat_id)
        if value and track and not self.queue.get(chat_id).upcoming:
            self._spawn(self._prefill(chat_id, track))
        await self.announcer.refresh(chat_id)
        return value

    # ------------------------------------------------------------------ teardown / idle
    async def _finish(self, chat_id: int) -> None:
        """Queue ran dry: stay connected briefly (instant replay), then leave."""
        q = self.queue.get(chat_id)
        last = q.last
        if last:
            await self.announcer.finished(chat_id, last)
        self.calls.pop(chat_id, None)
        q.reset()
        if config.IDLE_LEAVE_SECONDS > 0:
            self._idle[chat_id] = self._spawn(self._idle_leave(chat_id))
        else:
            await self._teardown(chat_id)

    async def _idle_leave(self, chat_id: int) -> None:
        try:
            await asyncio.sleep(config.IDLE_LEAVE_SECONDS)
        except asyncio.CancelledError:
            return
        async with self.lock(chat_id):
            if self.current(chat_id) is None:
                self._idle.pop(chat_id, None)
                await self._teardown(chat_id)
                await self.announcer.notice(chat_id, "idle_left")

    async def _teardown(self, chat_id: int) -> None:
        self._cancel_idle(chat_id)
        q = self.queue.peek(chat_id)
        if q and q.current:
            await self.announcer.finished(chat_id, q.current)
        self.queue.drop(chat_id)
        self.calls.pop(chat_id, None)
        try:
            await self.engine.leave(chat_id)
        except Exception:
            pass

    async def shutdown(self) -> None:
        for t in list(self._idle.values()) + list(self._bg):
            t.cancel()
