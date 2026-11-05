"""Логика тестов: запуск (рассылка всем), ответы, остановка (рассылка мест)."""
from __future__ import annotations

import asyncio
import logging

from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter, TelegramBadRequest
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from . import db
from .quizzes import BY_ID

log = logging.getLogger(__name__)
SEND_DELAY = 0.05  # ~20 сообщений в секунду — с запасом до лимита Telegram (30/с)
LETTERS = "АБВГ"
_tasks: set[asyncio.Task] = set()


def _bg(coro) -> None:
    t = asyncio.create_task(coro)
    _tasks.add(t)
    t.add_done_callback(_tasks.discard)


async def _send(bot: Bot, chat_id: int, text: str, **kw) -> bool:
    for _ in range(3):
        try:
            await bot.send_message(chat_id, text, **kw)
            return True
        except TelegramRetryAfter as e:
            await asyncio.sleep(e.retry_after + 1)
        except (TelegramForbiddenError, TelegramBadRequest) as e:
            log.info("Не доставлено %s: %s", chat_id, e)
            return False
    return False


def question_text(quiz: dict, q_index: int) -> str:
    q = quiz["questions"][q_index]
    lines = [f"Вопрос {q_index + 1} из {len(quiz['questions'])}", "", q["q"], ""]
    lines += [f"{LETTERS[i]}) {opt}" for i, opt in enumerate(q["options"])]
    return "\n".join(lines)


def question_keyboard(run_id: int, q_index: int, quiz: dict) -> InlineKeyboardMarkup:
    q = quiz["questions"][q_index]
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"{LETTERS[i]}) {opt}"[:60], callback_data=f"qa:{run_id}:{q_index}:{i}")]
        for i, opt in enumerate(q["options"])
    ])


def answered_text(quiz: dict, q_index: int, chosen: int) -> str:
    q = quiz["questions"][q_index]
    ok = chosen == q["correct"]
    lines = [f"Вопрос {q_index + 1} из {len(quiz['questions'])}", "", q["q"], ""]
    for i, opt in enumerate(q["options"]):
        mark = "✅" if i == q["correct"] else ("❌" if i == chosen else "▫️")
        lines.append(f"{mark} {LETTERS[i]}) {opt}")
    lines += ["", "Верно!" if ok else f"Неверно. Правильный ответ: {LETTERS[q['correct']]}) {q['options'][q['correct']]}",
              q["explain"]]
    return "\n".join(lines)


async def send_question(bot: Bot, chat_id: int, run_id: int, quiz: dict, q_index: int) -> bool:
    return await _send(bot, chat_id, question_text(quiz, q_index),
                       reply_markup=question_keyboard(run_id, q_index, quiz))


async def invite(bot: Bot, run_id: int, tg_id: int) -> bool:
    quiz = BY_ID[db.get_run(run_id)["quiz_id"]]
    if not db.add_participant(run_id, tg_id):
        return False
    ok = await _send(bot, tg_id, f"Начинается тест по истории России: «{quiz['title']}»\n"
                                 f"{len(quiz['questions'])} вопросов. Отвечайте кнопками под вопросом. Удачи!")
    ok = ok and await send_question(bot, tg_id, run_id, quiz, 0)
    db.mark_delivery(run_id, tg_id, ok)
    return ok


# ---------- Запуск / остановка (вызываются из панели) ----------

def start(bot: Bot, quiz_id: int) -> int:
    if quiz_id not in BY_ID:
        raise RuntimeError("Нет такого теста")
    run_id = db.create_run(quiz_id)
    db.log_event(None, "quiz_start", f"run {run_id}: {BY_ID[quiz_id]['title']}")
    _bg(_broadcast_start(bot, run_id))
    return run_id


async def _broadcast_start(bot: Bot, run_id: int) -> None:
    for u in db.registered_users():
        run = db.get_run(run_id)
        if run["status"] != "active":
            break
        await invite(bot, run_id, u["tg_id"])
        await asyncio.sleep(SEND_DELAY)


def stop(bot: Bot, run_id: int) -> None:
    ranked = db.stop_run(run_id)
    quiz = BY_ID[db.get_run(run_id)["quiz_id"]]
    db.log_event(None, "quiz_stop", f"run {run_id}: {quiz['title']}, участников {len(ranked)}")
    _bg(_broadcast_results(bot, run_id, quiz, ranked))


def place_text(quiz: dict, ranked: list, p) -> str:
    n = len(quiz["questions"])
    top = "\n".join(
        f"{i}. {r['name'] or 'Участник'} — {r['score']} из {n}" for i, r in enumerate(ranked[:3], 1))
    me = next((i for i, r in enumerate(ranked, 1) if r["tg_id"] == p["tg_id"]), None)
    if me is None:
        return (f"Тест «{quiz['title']}» завершён.\n"
                f"Вы не успели ответить — ждём вас в следующем тесте!\n\nЛучшие:\n{top or '—'}")
    medal = {1: "🥇 ", 2: "🥈 ", 3: "🥉 "}.get(me, "")
    partial = "" if p["current_q"] >= n else f" (отвечено {p['current_q']} из {n})"
    return (f"Тест «{quiz['title']}» завершён!\n\n"
            f"{medal}Ваше место: {me} из {len(ranked)}\n"
            f"Ваш результат: {p['score']} из {n}{partial}\n\n"
            f"Лучшие:\n{top}")


async def _broadcast_results(bot: Bot, run_id: int, quiz: dict, ranked: list) -> None:
    for p in db.run_participants(run_id):
        if p["delivered"]:
            await _send(bot, p["tg_id"], place_text(quiz, ranked, p))
            await asyncio.sleep(SEND_DELAY)


# ---------- Ответ участника ----------

async def answer(bot: Bot, tg_id: int, run_id: int, q_index: int, chosen: int) -> tuple[str, str | None]:
    """Возвращает (всплывающая подсказка, новый текст сообщения или None)."""
    run = db.get_run(run_id)
    if run is None or run["status"] != "active":
        return "Этот тест уже завершён", None
    quiz = BY_ID[run["quiz_id"]]
    n = len(quiz["questions"])
    if not 0 <= q_index < n or not 0 <= chosen < 4:
        return "Некорректный ответ", None
    ok = chosen == quiz["questions"][q_index]["correct"]
    if not db.record_answer(run_id, tg_id, q_index, chosen, ok, n):
        return "Вы уже ответили на этот вопрос", None

    text = answered_text(quiz, q_index, chosen)
    if q_index + 1 < n:
        _bg(_next(bot, tg_id, run_id, quiz, q_index + 1))
    else:
        p = db.get_participant(run_id, tg_id)
        _bg(_send(bot, tg_id, f"Тест «{quiz['title']}» пройден!\n\n"
                              f"Ваш результат: {p['score']} из {n} правильных.\n"
                              f"Место в рейтинге пришлю, когда тест будет остановлен."))
    return ("Верно!" if ok else "Неверно"), text


async def _next(bot: Bot, tg_id: int, run_id: int, quiz: dict, q_index: int) -> None:
    await asyncio.sleep(0.3)  # чтобы разбор ответа появился раньше следующего вопроса
    await send_question(bot, tg_id, run_id, quiz, q_index)
