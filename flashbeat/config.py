from __future__ import annotations

import os

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # python-dotenv is optional
    pass


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except ValueError:
        return default


class Config:
    def __init__(self) -> None:
        # --- required ---------------------------------------------------
        self.API_ID = _int("API_ID", 0)
        self.API_HASH = os.getenv("API_HASH", "")
        self.BOT_TOKEN = os.getenv("BOT_TOKEN", "")
        self.OWNER_ID = _int("OWNER_ID", 0)
        self.LOGGER_ID = _int("LOGGER_ID", 0)
        self.SESSIONS = [
            s for s in (os.getenv(k, "").strip() for k in
                        ("SESSION", "SESSION2", "SESSION3", "SESSION4", "SESSION5")) if s
        ]

        # --- storage ----------------------------------------------------
        # Leave MONGO_URL empty to use a local JSON file (data/state.json).
        self.MONGO_URL = os.getenv("MONGO_URL", "").strip()
        self.DATA_DIR = os.getenv("DATA_DIR", "data")

        # --- limits -----------------------------------------------------
        self.DURATION_LIMIT = _int("DURATION_LIMIT", 180) * 60      # minutes -> seconds
        self.QUEUE_LIMIT = _int("QUEUE_LIMIT", 50)
        self.PLAYLIST_LIMIT = _int("PLAYLIST_LIMIT", 25)
        self.TG_FILE_LIMIT_MB = _int("TG_FILE_LIMIT_MB", 200)

        # --- speed knobs ------------------------------------------------
        self.PREFETCH = _bool("PREFETCH", True)                    # resolve next track early
        self.CACHE_DOWNLOADS = _bool("CACHE_DOWNLOADS", True)      # background file copy of current track
        self.CACHE_MAX_MB = _int("CACHE_MAX_MB", 1500)             # disk cache size cap
        self.YTDLP_WORKERS = _int("YTDLP_WORKERS", 4)
        self.RESOLVE_TIMEOUT = _int("RESOLVE_TIMEOUT", 30)
        self.DOWNLOAD_TIMEOUT = _int("DOWNLOAD_TIMEOUT", 300)
        self.FFMPEG_FAST_PROBE = _bool("FFMPEG_FAST_PROBE", False) # smaller probe -> quicker start
        self.YT_PLAYER_CLIENTS = [
            c.strip() for c in os.getenv("YT_PLAYER_CLIENTS", "").split(",") if c.strip()
        ]

        # --- behaviour --------------------------------------------------
        self.IDLE_LEAVE_SECONDS = _int("IDLE_LEAVE_SECONDS", 300)  # 0 = never leave on idle
        self.THUMB_GEN = _bool("THUMB_GEN", True)
        self.VIDEO_PLAY = _bool("VIDEO_PLAY", True)
        self.COOKIES_URL = [u for u in os.getenv("COOKIES_URL", "").split() if u.startswith("https://")]

        # --- links / images --------------------------------------------
        self.SUPPORT_CHAT = os.getenv("SUPPORT_CHAT", "")
        self.SUPPORT_CHANNEL = os.getenv("SUPPORT_CHANNEL", "")
        self.START_IMG = os.getenv("START_IMG", "")

    def check(self) -> None:
        missing = [
            name for name, val in (
                ("API_ID", self.API_ID), ("API_HASH", self.API_HASH),
                ("BOT_TOKEN", self.BOT_TOKEN), ("OWNER_ID", self.OWNER_ID),
                ("LOGGER_ID", self.LOGGER_ID), ("SESSION", self.SESSIONS),
            ) if not val
        ]
        if missing:
            raise SystemExit("Missing required environment variables: " + ", ".join(missing))


config = Config()
