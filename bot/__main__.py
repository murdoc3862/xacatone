"""Запуск: python -m bot  (бот + веб-панель в одном процессе)."""
import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiohttp import web as aioweb

from . import config, db, handlers, monitor, web

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("bot")


async def main() -> None:
    cfg = config.load()
    db.init()

    bot = Bot(cfg.token)
    dp = Dispatcher()
    dp.update.outer_middleware(monitor.UpdateMiddleware())
    dp.errors.register(monitor.on_error)
    dp.include_router(handlers.router)

    async def on_startup():
        monitor.state["polling"] = True

    async def on_shutdown():
        monitor.state["polling"] = False

    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)

    runner = aioweb.AppRunner(web.create_app(cfg))
    await runner.setup()
    await aioweb.TCPSite(runner, cfg.host, cfg.port).start()
    log.info("Панель: http://%s:%s/admin  |  Состояние: http://%s:%s/status",
             cfg.host, cfg.port, cfg.host, cfg.port)

    ping = asyncio.create_task(monitor.ping_loop(bot))
    try:
        await dp.start_polling(bot)
    finally:
        ping.cancel()
        await runner.cleanup()
        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
