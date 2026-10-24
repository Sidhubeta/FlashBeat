class NoVoiceChat(Exception):
    """The group has no active voice chat."""


class TelegramDown(Exception):
    """Telegram-side connection problem (or unsupported RTMP chat)."""


class StreamFailed(Exception):
    """This particular source could not be played (bad URL, no audio, ...)."""
