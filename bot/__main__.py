"""Запуск: python -m bot  (бот + веб-панель в одном процессе).

Если в .env задан DOMAIN — Telegram присылает сообщения на https://DOMAIN/tg/webhook,
иначе бот сам опрашивает Telegram (polling) — так удобно запускать локально.
"""
import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
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
    app = web.create_app(cfg, bot)

    if cfg.domain:
        url = f"https://{cfg.domain}{web.WEBHOOK_PATH}"
        monitor.state.update(mode="webhook", webhook_url=url)

        async def on_startup(bot: Bot):
            await bot.set_webhook(url, secret_token=cfg.webhook_secret or None,
                                  allowed_updates=dp.resolve_used_update_types())
            monitor.state["polling"] = True
            log.info("Webhook установлен: %s", url)

        dp.startup.register(on_startup)
        SimpleRequestHandler(dispatcher=dp, bot=bot,
                             secret_token=cfg.webhook_secret or None).register(app, path=web.WEBHOOK_PATH)
        setup_application(app, dp, bot=bot)
    else:
        async def on_startup():
            monitor.state["polling"] = True

        async def on_shutdown():
            monitor.state["polling"] = False

        dp.startup.register(on_startup)
        dp.shutdown.register(on_shutdown)

    runner = aioweb.AppRunner(app)
    await runner.setup()
    await aioweb.TCPSite(runner, cfg.host, cfg.port).start()
    base = f"https://{cfg.domain}" if cfg.domain else f"http://{cfg.host}:{cfg.port}"
    log.info("Панель: %s/admin  |  Состояние: %s/status", base, base)

    ping = asyncio.create_task(monitor.ping_loop(bot))
    try:
        if cfg.domain:
            await asyncio.Event().wait()  # работаем, пока процесс не остановят
        else:
            await bot.delete_webhook()
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
