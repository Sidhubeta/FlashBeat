"""FlashBeat - a stream-first Telegram voice chat music bot."""

import logging
from logging.handlers import RotatingFileHandler

__version__ = "1.0.0"

logging.basicConfig(
    format="[%(asctime)s %(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        RotatingFileHandler("flashbeat.log", maxBytes=5_000_000, backupCount=3, encoding="utf-8"),
        logging.StreamHandler(),
    ],
    level=logging.INFO,
)
for noisy in ("httpx", "ntgcalls", "pymongo", "pyrogram", "pytgcalls", "asyncio"):
    logging.getLogger(noisy).setLevel(logging.ERROR)

logger = logging.getLogger("flashbeat")
