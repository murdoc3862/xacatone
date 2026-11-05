import os
import sys
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


@dataclass
class Config:
    token: str
    admin_login: str
    admin_password: str
    host: str
    port: int
    domain: str          # если задан — бот работает через webhook на https://<domain>
    webhook_secret: str


def load() -> Config:
    token = os.getenv("BOT_TOKEN", "").strip()
    if not token or "ВАШ_ТОКЕН" in token:
        sys.exit("Ошибка: не указан BOT_TOKEN в файле .env (см. .env.example)")
    password = os.getenv("ADMIN_PASSWORD", "").strip()
    if not password or "придумайте" in password:
        sys.exit("Ошибка: задайте ADMIN_PASSWORD в файле .env")
    return Config(
        token=token,
        admin_login=os.getenv("ADMIN_LOGIN", "admin").strip(),
        admin_password=password,
        host=os.getenv("WEB_HOST", "127.0.0.1").strip(),
        port=int(os.getenv("WEB_PORT", "8080")),
        domain=os.getenv("DOMAIN", "").strip(),
        webhook_secret=os.getenv("WEBHOOK_SECRET", "").strip(),
    )
