#!/bin/bash
# Выкладка/обновление с Mac: bash deploy/deploy.sh root@IP домен [email]
set -euo pipefail
cd "$(dirname "$0")/.."
SERVER="$1"; DOMAIN="$2"; EMAIL="${3:-}"
ssh "$SERVER" "mkdir -p /opt/webinar-bot"
rsync -az --delete \
  --exclude '.venv*' --exclude '__pycache__' --exclude 'data/' --exclude '.git' \
  ./ "$SERVER:/opt/webinar-bot/"
ssh "$SERVER" "bash /opt/webinar-bot/deploy/setup_server.sh '$DOMAIN' '$EMAIL'"
