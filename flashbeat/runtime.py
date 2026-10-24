"""Builds the singletons. Imported from inside the running event loop (see __main__)."""
import time
from collections import deque

from flashbeat.config import config
from flashbeat.core.announcer import Announcer
from flashbeat.core.assistants import Assistants
from flashbeat.core.bot import Bot
from flashbeat.core.cache import TTLCache
from flashbeat.core.cards import Cards
from flashbeat.core.engine import Engine
from flashbeat.core.player import Player
from flashbeat.core.queue import QueueManager
from flashbeat.core.resolver import Resolver
from flashbeat.core.store import JsonBackend, MongoBackend, Store
from flashbeat.helpers.perms import Perms

boot_time = time.time()

bot = Bot()
store = Store(MongoBackend(config.MONGO_URL) if config.MONGO_URL
              else JsonBackend(f"{config.DATA_DIR}/state.json"))
resolver = Resolver()
queue = QueueManager()
assistants = Assistants(store)
engine = Engine(assistants)
cards = Cards(resolver)
announcer = Announcer(bot, cards, store, queue)
player = Player(engine, queue, store, resolver, announcer)
announcer.player = player
perms = Perms(bot, store)

joined = TTLCache(4096, ttl=900)       # chats where the assistant is known to be present
tokens = TTLCache(512, ttl=900)        # short-lived payloads behind inline buttons
latency = deque(maxlen=50)             # seconds from /play to audio, for /ping
