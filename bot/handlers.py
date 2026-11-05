"""Сценарий: /start -> спросить имя -> сохранить.

Состояние «ждём имя» берётся из базы (name пустой), поэтому
регистрация не теряется даже после перезапуска бота.
"""
from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import CommandStart
from aiogram.types import Message

from . import db

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
        await message.answer("Вы уже зарегистрированы. Скоро здесь появятся новые возможности!")
        return

    name = " ".join(message.text.split())
    if name.startswith("/") or not 2 <= len(name) <= 50:
        await message.answer("Пожалуйста, напишите имя текстом (от 2 до 50 символов).")
        return
    db.set_name(u.id, name)
    db.log_event(u.id, "registered", name)
    await message.answer(f"Приятно познакомиться, {name}! Вы зарегистрированы.")


@router.message()
async def not_text(message: Message) -> None:
    await message.answer("Пожалуйста, отправьте текстовое сообщение.")
