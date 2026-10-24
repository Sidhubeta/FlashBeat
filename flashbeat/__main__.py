import asyncio
import importlib
import time

from flashbeat import __version__, logger
from flashbeat.config import config


async def main() -> None:
    config.check()
    t0 = time.perf_counter()
    from flashbeat import runtime as rt                       # built inside the running loop
    from flashbeat.plugins import MODULES

    for name in MODULES:                                      # register handlers before connecting
        importlib.import_module(f"flashbeat.plugins.{name}")

    await rt.store.load()
    # boot everything that doesn't depend on each other in parallel
    await asyncio.gather(rt.bot.boot(), rt.assistants.boot(), rt.resolver.start())
    await rt.engine.boot()
    rt.store.add_sudo(config.OWNER_ID)
    logger.info("FlashBeat %s ready in %.1fs", __version__, time.perf_counter() - t0)
    try:
        await rt.bot.send_message(config.LOGGER_ID, f"⚡ <b>FlashBeat {__version__}</b> is online "
                                                    f"({time.perf_counter() - t0:.1f}s boot).")
    except Exception as ex:
        logger.warning("cannot write to LOGGER_ID: %s", ex)

    try:
        await asyncio.Event().wait()
    finally:
        await rt.player.shutdown()
        await rt.resolver.close()
        await rt.assistants.stop()
        await rt.bot.exit()
        await rt.store.close()
        logger.info("stopped")


if __name__ == "__main__":
    try:
        import uvloop

        uvloop.install()
    except ImportError:
        pass
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
