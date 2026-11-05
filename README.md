# Телеграм-бот «Крутой Вебинар»

Статус: **бот работает на сервере (VPS Timeweb)**.

## Что умеет
- `/start` → бот спрашивает имя → сохраняет пользователя в базу
- **Админ-панель** `http://127.0.0.1:8080/admin`: все зарегистрированные пользователи, поиск, выгрузка в CSV (Excel)
- **Панель состояния** `http://127.0.0.1:8080/status`: работает ли бот, есть ли связь с Telegram, база, ошибки, последние события
- `http://127.0.0.1:8080/health`: JSON-проверка для внешнего мониторинга (без пароля)

## Запуск на Mac
1. Получите токен у @BotFather (`/newbot`).
2. Дважды кликните `start.command` (при первом запуске он создаст `.env` и откроет его).
3. Впишите в `.env` значения `BOT_TOKEN` и `ADMIN_PASSWORD`, сохраните файл и снова запустите `start.command`.
4. Откройте http://127.0.0.1:8080/admin (логин `admin`, пароль из `.env`).

Вручную: `python3 -m venv .venv && .venv/bin/pip install -r requirements.txt && .venv/bin/python -m bot`

## Выкладка на сервер (через git)
```
git add -A && git commit -m "что изменилось"
SSH_KEY=.secrets/claude_deploy bash deploy/deploy.sh root@IP_СЕРВЕРА
```
Скрипт отправит коммиты в GitHub, сервер заберёт их оттуда и перезапустит бота. `.env` и база на сервере не трогаются.

## Структура
```
bot/__main__.py   запуск: бот + веб-панель в одном процессе
bot/handlers.py   сценарий регистрации
bot/db.py         база SQLite (data/bot.db): users, events
bot/web.py        страницы /admin, /status, /health
bot/monitor.py    проверки здоровья бота
bot/config.py     настройки из .env
deploy/           systemd, nginx, установка и выкладка на сервер
docs/             план, журнал изменений, решения, требования, аналитика
```
