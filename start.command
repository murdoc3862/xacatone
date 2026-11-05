#!/bin/bash
# Двойной клик по этому файлу в Finder запускает бота.
cd "$(dirname "$0")"
if [ ! -d .venv ]; then
  echo "Первый запуск: устанавливаю зависимости..."
  python3 -m venv .venv && .venv/bin/pip install -q -r requirements.txt
fi
if [ ! -f .env ]; then
  cp .env.example .env
  echo "Создан файл .env — впишите в него BOT_TOKEN и ADMIN_PASSWORD, затем запустите снова."
  open -e .env
  exit 1
fi
.venv/bin/python -m bot
