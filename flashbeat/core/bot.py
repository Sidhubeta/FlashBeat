import pyrogram

from flashbeat import logger
from flashbeat.config import config


class Bot(pyrogram.Client):
    def __init__(self) -> None:
        super().__init__(
            name="flashbeat",
            api_id=config.API_ID,
            api_hash=config.API_HASH,
            bot_token=config.BOT_TOKEN,
            parse_mode=pyrogram.enums.ParseMode.HTML,
            max_concurrent_transmissions=10,
            link_preview_options=pyrogram.types.LinkPreviewOptions(is_disabled=True),
        )

    async def boot(self) -> None:
        await super().start()
        me = self.me
        self.id, self.username, self.mention, self.display_name = me.id, me.username, me.mention, me.first_name
        logger.info("bot online as @%s", self.username)

    async def exit(self) -> None:
        await super().stop()
