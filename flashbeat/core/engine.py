"""Thin wrapper around py-tgcalls: builds the media stream and maps library errors to ours."""
from __future__ import annotations

import asyncio
import inspect
import os
from typing import Awaitable, Callable, Optional

from ntgcalls import ConnectionNotFound, RTMPStreamingUnsupported, TelegramServerError, TransportParseException
from ntgcalls import ConnectionError as NtgConnectionError
from pytgcalls import PyTgCalls, exceptions, types
from pytgcalls.pytgcalls_session import PyTgCallsSession

from flashbeat import logger
from flashbeat.config import config
from flashbeat.core.errors import NoVoiceChat, StreamFailed, TelegramDown
from flashbeat.core.models import Track

_HEADER_KEYS = {"user-agent", "referer", "origin", "accept-language", "cookie"}


class Engine:
    def __init__(self, assistants) -> None:
        self.assistants = assistants
        self.calls: dict[int, PyTgCalls] = {}
        self.on_stream_end: Optional[Callable[[int], Awaitable]] = None
        self.on_chat_closed: Optional[Callable[[int], Awaitable]] = None
        self._headers_supported: Optional[bool] = None

    async def boot(self) -> None:
        PyTgCallsSession.notice_displayed = True
        for ub in self.assistants.clients:
            client = PyTgCalls(ub, cache_duration=100)
            await client.start()
            self._register(client)
            self.calls[ub.fb_index] = client
        logger.info("voice engine ready (%d assistant(s))", len(self.calls))

    def _register(self, client: PyTgCalls) -> None:
        @client.on_update()
        async def _handler(_, update: types.Update) -> None:
            # never block py-tgcalls' update loop: hand work to our own tasks
            if isinstance(update, types.StreamEnded):
                if update.stream_type == types.StreamEnded.Type.AUDIO and self.on_stream_end:
                    asyncio.ensure_future(self.on_stream_end(update.chat_id))
            elif isinstance(update, types.ChatUpdate):
                if update.status in (types.ChatUpdate.Status.KICKED, types.ChatUpdate.Status.LEFT_GROUP,
                                     types.ChatUpdate.Status.CLOSED_VOICE_CHAT) and self.on_chat_closed:
                    asyncio.ensure_future(self.on_chat_closed(update.chat_id))

    def _client(self, chat_id: int) -> PyTgCalls:
        return self.calls[self.assistants.pick(chat_id).fb_index]

    # ------------------------------------------------------------------
    def _headers_ok(self) -> bool:
        if self._headers_supported is None:
            try:
                self._headers_supported = "headers" in inspect.signature(types.MediaStream.__init__).parameters
            except (TypeError, ValueError):
                self._headers_supported = False
        return self._headers_supported

    def _stream(self, track: Track, src: str, remote: bool, seek: int) -> types.MediaStream:
        params: list[str] = []
        if remote:
            params += ["-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "5"]
            if config.FFMPEG_FAST_PROBE:
                params += ["-probesize", "1M", "-analyzeduration", "1M"]
        if seek > 1:
            params += ["-ss", str(int(seek))]
        kwargs = dict(
            media_path=src,
            audio_parameters=types.AudioQuality.HIGH,
            video_parameters=types.VideoQuality.HD_720p,
            audio_flags=types.MediaStream.Flags.REQUIRED,
            video_flags=types.MediaStream.Flags.AUTO_DETECT if track.video else types.MediaStream.Flags.IGNORE,
            ffmpeg_parameters=" ".join(params) or None,
        )
        if remote and track.headers and self._headers_ok():
            kwargs["headers"] = {k: v for k, v in track.headers.items() if k.lower() in _HEADER_KEYS}
        return types.MediaStream(**kwargs)

    async def play(self, chat_id: int, track: Track, seek: int = 0) -> None:
        use_file = bool(track.file_path and os.path.exists(track.file_path))
        src = track.file_path if use_file else track.stream_url
        if not src:
            raise StreamFailed("no playable source")
        stream = self._stream(track, src, remote=not use_file, seek=seek)
        try:
            await self._client(chat_id).play(chat_id=chat_id, stream=stream,
                                             config=types.GroupCallConfig(auto_start=False))
        except exceptions.NoActiveGroupCall:
            raise NoVoiceChat()
        except RTMPStreamingUnsupported:
            raise TelegramDown("rtmp")
        except (NtgConnectionError, ConnectionNotFound, TelegramServerError, TransportParseException, TimeoutError) as ex:
            raise TelegramDown(str(ex))
        except (FileNotFoundError, exceptions.NoAudioSourceFound) as ex:
            raise StreamFailed(str(ex))
        except Exception as ex:                                   # anything else = this source is bad
            raise StreamFailed(f"{type(ex).__name__}: {ex}")
        track.via = "file" if use_file else "stream"

    async def pause(self, chat_id: int) -> None:
        try:
            await self._client(chat_id).pause(chat_id)
        except (ConnectionNotFound, exceptions.NotInCallError):
            raise NoVoiceChat()

    async def resume(self, chat_id: int) -> None:
        try:
            await self._client(chat_id).resume(chat_id)
        except (ConnectionNotFound, exceptions.NotInCallError):
            raise NoVoiceChat()

    async def leave(self, chat_id: int) -> None:
        await self._client(chat_id).leave_call(chat_id, close=False)

    async def set_volume(self, chat_id: int, volume: int) -> bool:
        fn = getattr(self._client(chat_id), "change_volume_call", None)
        if fn is None:
            return False
        try:
            await fn(chat_id, volume)
            return True
        except Exception as ex:
            logger.info("volume change failed: %s", ex)
            return False

    def ping(self) -> float:
        pings = [c.ping for c in self.calls.values()]
        return round(sum(pings) / len(pings), 2) if pings else 0.0
