"""Наблюдение за здоровьем бота: время последнего апдейта, связь с Telegram, ошибки."""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware, Bot
from aiogram.types import ErrorEvent, TelegramObject

from . import db

log = logging.getLogger(__name__)
PING_INTERVAL = 60  # секунд

state: dict[str, Any] = {
    "started_at": time.time(),
    "polling": False,
    "mode": "polling",          # polling | webhook
    "webhook_url": None,
    "webhook_info": None,
    "bot_username": None,
    "last_update_at": None,
    "last_ping_at": None,
    "last_ping_ok": None,
    "last_ping_error": None,
}


class UpdateMiddleware(BaseMiddleware):
    async def __call__(self, handler: Callable[..., Awaitable[Any]],
                       event: TelegramObject, data: dict[str, Any]) -> Any:
        state["last_update_at"] = time.time()
        return await handler(event, data)


async def on_error(event: ErrorEvent) -> bool:
    log.exception("Ошибка при обработке апдейта", exc_info=event.exception)
    user = getattr(getattr(event.update, "message", None), "from_user", None)
    db.log_event(user.id if user else None, "error",
                 f"{type(event.exception).__name__}: {event.exception}")
    return True


async def ping_loop(bot: Bot) -> None:
    """Раз в минуту проверяем, что Telegram отвечает с нашим токеном."""
    while True:
        try:
            me = await bot.get_me()
            state.update(bot_username=me.username, last_ping_ok=True, last_ping_error=None)
            if state["mode"] == "webhook":
                state["webhook_info"] = await bot.get_webhook_info()
        except Exception as e:  # noqa: BLE001
            state.update(last_ping_ok=False, last_ping_error=f"{type(e).__name__}: {e}")
            log.warning("Telegram недоступен: %s", e)
        state["last_ping_at"] = time.time()
        await asyncio.sleep(PING_INTERVAL)


def checks() -> list[dict]:
    """Список проверок для страницы состояния."""
    now = time.time()
    try:
        db_ok, db_msg = db.ping(), "SQLite отвечает"
    except Exception as e:  # noqa: BLE001
        db_ok, db_msg = False, str(e)

    ping_at = state["last_ping_at"]
    fresh = ping_at is not None and now - ping_at < PING_INTERVAL * 3
    tg_ok = bool(state["last_ping_ok"]) and fresh
    if ping_at is None:
        tg_msg = "проверка ещё не выполнялась"
    elif tg_ok:
        tg_msg = f"@{state['bot_username']} отвечает"
    else:
        tg_msg = state["last_ping_error"] or "давно не было проверки"

    if state["mode"] == "webhook":
        info = state["webhook_info"]
        if info is None:
            recv_ok, recv_msg = False, "webhook ещё не проверен"
        elif info.url != state["webhook_url"]:
            recv_ok, recv_msg = False, f"в Telegram указан другой адрес: {info.url or 'пусто'}"
        elif info.last_error_date and now - info.last_error_date.timestamp() < 600:
            recv_ok, recv_msg = False, f"Telegram не может достучаться: {info.last_error_message}"
        else:
            recv_ok = True
            recv_msg = f"webhook {info.url}, в очереди {info.pending_update_count}"
    else:
        recv_ok = state["polling"]
        recv_msg = "polling работает" if recv_ok else "polling остановлен"

    return [
        {"name": "Бот получает сообщения", "ok": recv_ok, "msg": recv_msg},
        {"name": "Связь с Telegram", "ok": tg_ok, "msg": tg_msg},
        {"name": "База данных", "ok": db_ok, "msg": db_msg},
    ]
