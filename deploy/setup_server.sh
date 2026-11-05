#!/bin/bash
# Запускается на сервере от root: bash setup_server.sh [домен] [email-для-сертификата]
# Без домена панель доступна по IP (http), HTTPS не выпускается
# Код уже должен лежать в /opt/webinar-bot (его туда забирает из GitHub deploy.sh)
set -euo pipefail
DOMAIN="${1:-}"; EMAIL="${2:-}"
APP=/opt/webinar-bot

export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get install -y -q git python3 python3-venv python3-pip nginx certbot python3-certbot-nginx ufw

id botuser &>/dev/null || useradd --system --home "$APP" --shell /usr/sbin/nologin botuser
mkdir -p "$APP/data"
[ -d "$APP/.venv" ] || python3 -m venv "$APP/.venv"
"$APP/.venv/bin/pip" install -q --upgrade pip
"$APP/.venv/bin/pip" install -q -r "$APP/requirements.txt"
chown -R botuser:botuser "$APP"
chmod 600 "$APP/.env"

cp "$APP/deploy/webinar-bot.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now webinar-bot
systemctl restart webinar-bot

sed "s/__DOMAIN__/${DOMAIN:-_}/" "$APP/deploy/nginx.conf" > /etc/nginx/sites-available/webinar-bot
ln -sf /etc/nginx/sites-available/webinar-bot /etc/nginx/sites-enabled/webinar-bot
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl reload nginx

ufw allow OpenSSH >/dev/null; ufw allow 'Nginx Full' >/dev/null; ufw --force enable >/dev/null

if [ -z "$DOMAIN" ]; then
  echo "Домен не указан — HTTPS пропущен, панель доступна по http://IP/"
elif [ -n "$EMAIL" ]; then
  certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos -m "$EMAIL" --redirect || \
    echo "!!! HTTPS не выпущен: проверьте, что домен $DOMAIN указывает на IP этого сервера"
else
  certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos --register-unsafely-without-email --redirect || \
    echo "!!! HTTPS не выпущен: проверьте, что домен $DOMAIN указывает на IP этого сервера"
fi

sleep 3
systemctl --no-pager status webinar-bot | head -5
curl -s http://127.0.0.1:8080/health; echo
