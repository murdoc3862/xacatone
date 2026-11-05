"""Сценарий: /start -> спросить имя -> сохранить.

Состояние «ждём имя» берётся из базы (name пустой), поэтому
регистрация не теряется даже после перезапуска бота.
"""
from __future__ import annotations

from aiogram import Bot, F, Router
from aiogram.filters import CommandStart
from aiogram.types import CallbackQuery, Message

from . import db, quiz

router = Router()


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    u = message.from_user
    db.touch_user(u.id, u.username, u.first_name, u.last_name)
    db.log_event(u.id, "start")
    user = db.get_user(u.id)
    if user["name"]:
        await message.answer(f"С возвращением, {user['name']}! Вы уже зарегистрированы.")
        return
    await message.answer("Привет! Как вас зовут? Напишите, пожалуйста, ваше имя.")


@router.message(F.text)
async def any_text(message: Message) -> None:
    u = message.from_user
    user = db.get_user(u.id)
    if user is None:
        await message.answer("Нажмите /start, чтобы начать.")
        return
    if user["name"]:
        await message.answer("Вы зарегистрированы. Когда начнётся тест по истории, я пришлю вопросы сюда.")
        return

    name = " ".join(message.text.split())
    if name.startswith("/") or not 2 <= len(name) <= 50:
        await message.answer("Пожалуйста, напишите имя текстом (от 2 до 50 символов).")
        return
    db.set_name(u.id, name)
    db.log_event(u.id, "registered", name)
    await message.answer(f"Приятно познакомиться, {name}! Вы зарегистрированы.\n"
                         f"Когда начнётся тест по истории, я пришлю вопросы сюда.")
    run = db.active_run()
    if run:  # тест уже идёт — подключаем нового участника сразу
        await quiz.invite(message.bot, run["id"], u.id)


@router.callback_query(F.data.startswith("qa:"))
async def on_answer(cb: CallbackQuery, bot: Bot) -> None:
    try:
        _, run_id, q_index, chosen = cb.data.split(":")
        run_id, q_index, chosen = int(run_id), int(q_index), int(chosen)
    except ValueError:
        await cb.answer()
        return
    hint, text = await quiz.answer(bot, cb.from_user.id, run_id, q_index, chosen)
    await cb.answer(hint)
    if text and cb.message:
        await cb.message.edit_text(text)
    elif cb.message and hint == "Этот тест уже завершён":
        await cb.message.edit_reply_markup(reply_markup=None)


@router.message()
async def not_text(message: Message) -> None:
    await message.answer("Пожалуйста, отправьте текстовое сообщение.")
