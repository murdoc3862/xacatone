#!/bin/bash
# Выкладка через git: коммиты отправляются в GitHub, сервер забирает их оттуда.
#   bash deploy/deploy.sh root@IP [домен] [email]
#   SSH_KEY=.secrets/claude_deploy bash deploy/deploy.sh root@IP
# Первый запуск ставит всё с нуля, дальше — только обновляет код и перезапускает бота.
# .env и база data/ на сервере не трогаются (их нет в git).
set -euo pipefail
cd "$(dirname "$0")/.."
SERVER="$1"; DOMAIN="${2:-}"; EMAIL="${3:-}"
REPO="https://github.com/murdoc3862/xacatone.git"
APP=/opt/webinar-bot
SSH="ssh -o StrictHostKeyChecking=accept-new ${SSH_KEY:+-i $SSH_KEY -o IdentitiesOnly=yes}"

if [ -n "$(git status --porcelain)" ]; then
  echo "Есть незакоммиченные изменения — сначала сделайте commit"; git status --short; exit 1
fi
echo "==> git push"
git push -q origin main

echo "==> Сервер: git pull"
$SSH "$SERVER" bash -s <<REMOTE
set -euo pipefail
command -v git >/dev/null || (apt-get update -qq && apt-get install -y -qq git)
git config --global --add safe.directory $APP 2>/dev/null || true
if [ ! -d $APP/.git ]; then
  mkdir -p $APP && cd $APP
  git init -q -b main && git remote add origin $REPO
fi
cd $APP
git fetch -q origin main
git reset -q --hard origin/main
echo "Версия на сервере: \$(git log --oneline -1)"
REMOTE

if ! $SSH "$SERVER" "test -f $APP/.env"; then
  echo "==> Копирую .env (на сервере его ещё нет)"
  scp -q ${SSH_KEY:+-i $SSH_KEY -o IdentitiesOnly=yes} .env "$SERVER:$APP/.env"
fi

if $SSH "$SERVER" "systemctl is-enabled webinar-bot >/dev/null 2>&1"; then
  echo "==> Обновляю зависимости и перезапускаю бота"
  $SSH "$SERVER" "$APP/.venv/bin/pip install -q -r $APP/requirements.txt && chown -R botuser:botuser $APP && chmod 600 $APP/.env && systemctl restart webinar-bot && sleep 4 && systemctl is-active webinar-bot && curl -s http://127.0.0.1:8080/health"
else
  echo "==> Первая установка"
  $SSH "$SERVER" "bash $APP/deploy/setup_server.sh '$DOMAIN' '$EMAIL'"
fi
echo; echo "Готово."
