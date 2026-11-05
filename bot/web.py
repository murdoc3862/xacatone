"""Веб-панель: /admin — зарегистрированные пользователи, /status — проверка работы бота,
/health — JSON для внешнего мониторинга (без пароля)."""
from __future__ import annotations

import base64
import csv
import hmac
import io
import time
from datetime import datetime
from html import escape

from aiohttp import web

from . import db, monitor
from .config import Config

STYLE = """
<style>
:root{--bg:#f6f7f9;--card:#fff;--text:#1d2330;--muted:#6b7280;--line:#e5e7eb;--ok:#15803d;--bad:#b91c1c;--accent:#2563eb}
@media (prefers-color-scheme:dark){:root{--bg:#111318;--card:#1a1d24;--text:#e6e8ec;--muted:#9aa1ad;--line:#2a2e37;--ok:#4ade80;--bad:#f87171;--accent:#60a5fa}}
*{box-sizing:border-box}body{margin:0;font:15px/1.5 -apple-system,system-ui,sans-serif;background:var(--bg);color:var(--text)}
header{display:flex;gap:20px;align-items:center;padding:14px 24px;background:var(--card);border-bottom:1px solid var(--line)}
header b{font-size:17px}header a{color:var(--muted);text-decoration:none}header a.on{color:var(--accent);font-weight:600}
main{max-width:1100px;margin:0 auto;padding:24px 16px}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px;margin-bottom:20px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 16px}
.card .v{font-size:26px;font-weight:700}.card .l{color:var(--muted);font-size:13px}
table{width:100%;border-collapse:collapse;background:var(--card);border:1px solid var(--line);border-radius:10px;overflow:hidden}
th,td{text-align:left;padding:9px 12px;border-bottom:1px solid var(--line);vertical-align:top}th{font-size:13px;color:var(--muted);font-weight:600}
.wrap{overflow-x:auto}.muted{color:var(--muted)}.ok{color:var(--ok)}.bad{color:var(--bad)}
.dot{display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:8px}.dot.ok{background:var(--ok)}.dot.bad{background:var(--bad)}
.banner{padding:14px 18px;border-radius:10px;font-weight:600;margin-bottom:20px;border:1px solid var(--line);background:var(--card)}
h2{font-size:16px;margin:24px 0 10px}a.btn{color:var(--accent)}input{padding:7px 10px;border:1px solid var(--line);border-radius:8px;background:var(--card);color:var(--text);width:260px;max-width:100%}
</style>"""


def page(title: str, active: str, body: str, refresh: int | None = None) -> web.Response:
    meta = f'<meta http-equiv="refresh" content="{refresh}">' if refresh else ""
    nav = "".join(
        f'<a href="{href}" class="{"on" if key == active else ""}">{label}</a>'
        for key, href, label in [("admin", "/admin", "Пользователи"), ("status", "/status", "Состояние бота")]
    )
    html = (f'<!doctype html><html lang="ru"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1">{meta}'
            f'<title>{title}</title>{STYLE}</head><body>'
            f'<header><b>Крутой Вебинар</b>{nav}</header><main>{body}</main></body></html>')
    return web.Response(text=html, content_type="text/html")


def fmt_ts(ts: float | None) -> str:
    if ts is None:
        return "—"
    ago = int(time.time() - ts)
    when = datetime.fromtimestamp(ts).strftime("%d.%m %H:%M:%S")
    return f"{when} <span class='muted'>({fmt_dur(ago)} назад)</span>"


def fmt_dur(sec: int) -> str:
    d, sec = divmod(sec, 86400)
    h, sec = divmod(sec, 3600)
    m, s = divmod(sec, 60)
    if d:
        return f"{d} д {h} ч"
    if h:
        return f"{h} ч {m} мин"
    if m:
        return f"{m} мин"
    return f"{s} с"


def make_auth(cfg: Config):
    expected = "Basic " + base64.b64encode(f"{cfg.admin_login}:{cfg.admin_password}".encode()).decode()

    @web.middleware
    async def auth(request: web.Request, handler):
        if request.path in ("/health",):
            return await handler(request)
        got = request.headers.get("Authorization", "")
        if not hmac.compare_digest(got.encode(), expected.encode()):
            return web.Response(status=401, text="Нужен логин и пароль",
                                headers={"WWW-Authenticate": 'Basic realm="admin", charset="UTF-8"'})
        return await handler(request)

    return auth


async def admin(request: web.Request) -> web.Response:
    users = db.list_users()
    s = db.stats()
    rows = []
    for i, u in enumerate(users, 1):
        uname = f'<a class="btn" href="https://t.me/{escape(u["username"])}" target="_blank">@{escape(u["username"])}</a>' if u["username"] else "<span class='muted'>—</span>"
        tg_name = escape(" ".join(x for x in (u["first_name"], u["last_name"]) if x))
        name = escape(u["name"]) if u["name"] else "<span class='muted'>ещё не ввёл имя</span>"
        rows.append(
            f"<tr><td>{i}</td><td><b>{name}</b></td><td>{uname}</td><td>{tg_name}</td>"
            f"<td class='muted'>{u['tg_id']}</td><td>{u['registered_at'] or '—'}</td><td class='muted'>{u['started_at']}</td></tr>")
    table = "".join(rows) or "<tr><td colspan='7' class='muted'>Пока никто не зарегистрировался</td></tr>"
    conv = f"{round(s['registered'] / s['started'] * 100)}%" if s["started"] else "—"
    body = f"""
    <div class="cards">
      <div class="card"><div class="v">{s['registered']}</div><div class="l">Зарегистрировано</div></div>
      <div class="card"><div class="v">{s['registered_today']}</div><div class="l">Сегодня</div></div>
      <div class="card"><div class="v">{s['started']}</div><div class="l">Нажали /start</div></div>
      <div class="card"><div class="v">{conv}</div><div class="l">Конверсия start → имя</div></div>
    </div>
    <p style="display:flex;gap:16px;align-items:center;flex-wrap:wrap">
      <input id="q" placeholder="Поиск по имени или username" oninput="f()">
      <a class="btn" href="/admin/users.csv">Скачать CSV (Excel)</a></p>
    <div class="wrap"><table id="t"><thead><tr><th>#</th><th>Имя</th><th>Username</th><th>Имя в Telegram</th>
      <th>Telegram ID</th><th>Зарегистрирован</th><th>Первый /start</th></tr></thead><tbody>{table}</tbody></table></div>
    <script>function f(){{const q=document.getElementById('q').value.toLowerCase();
    document.querySelectorAll('#t tbody tr').forEach(r=>r.style.display=r.innerText.toLowerCase().includes(q)?'':'none')}}</script>"""
    return page("Пользователи", "admin", body)


async def users_csv(request: web.Request) -> web.Response:
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(["Имя", "Username", "Имя в Telegram", "Фамилия в Telegram", "Telegram ID", "Зарегистрирован", "Первый /start"])
    for u in db.list_users():
        w.writerow([u["name"] or "", u["username"] or "", u["first_name"] or "", u["last_name"] or "",
                    u["tg_id"], u["registered_at"] or "", u["started_at"]])
    return web.Response(body=("﻿" + buf.getvalue()).encode("utf-8"), content_type="text/csv",
                        headers={"Content-Disposition": 'attachment; filename="users.csv"'})


async def status(request: web.Request) -> web.Response:
    checks = monitor.checks()
    all_ok = all(c["ok"] for c in checks)
    st = monitor.state
    s = db.stats()
    banner = ('<div class="banner ok"><span class="dot ok"></span>Бот работает нормально</div>' if all_ok else
              '<div class="banner bad"><span class="dot bad"></span>Есть проблемы — смотрите красные пункты</div>')
    rows = "".join(
        f"<tr><td><span class='dot {'ok' if c['ok'] else 'bad'}'></span>{c['name']}</td>"
        f"<td class='{'ok' if c['ok'] else 'bad'}'>{'OK' if c['ok'] else 'ОШИБКА'}</td><td class='muted'>{escape(str(c['msg']))}</td></tr>"
        for c in checks)
    labels = {"start": "/start", "registered": "Регистрация", "error": "Ошибка"}
    ev = "".join(
        f"<tr><td class='muted'>{e['ts']}</td><td class='{'bad' if e['type'] == 'error' else ''}'>{labels.get(e['type'], e['type'])}</td>"
        f"<td class='muted'>{e['tg_id'] or ''}</td><td>{escape(e['details'] or '')}</td></tr>"
        for e in db.last_events()) or "<tr><td colspan='4' class='muted'>Событий пока нет</td></tr>"
    body = f"""{banner}
    <div class="cards">
      <div class="card"><div class="v">{fmt_dur(int(time.time() - st['started_at']))}</div><div class="l">Работает без перезапуска</div></div>
      <div class="card"><div class="v">{s['registered']}</div><div class="l">Пользователей</div></div>
      <div class="card"><div class="v {'bad' if s['errors_24h'] else ''}">{s['errors_24h']}</div><div class="l">Ошибок за 24 ч</div></div>
    </div>
    <h2>Проверки</h2><div class="wrap"><table><tbody>{rows}</tbody></table></div>
    <h2>Активность</h2><div class="wrap"><table><tbody>
      <tr><td>Последнее сообщение от пользователя</td><td>{fmt_ts(st['last_update_at'])}</td></tr>
      <tr><td>Последняя проверка связи с Telegram</td><td>{fmt_ts(st['last_ping_at'])}</td></tr>
    </tbody></table></div>
    <h2>Последние события</h2><div class="wrap"><table><thead><tr><th>Время</th><th>Событие</th><th>Telegram ID</th><th>Детали</th></tr></thead><tbody>{ev}</tbody></table></div>
    <p class="muted">Страница обновляется сама каждые 30 секунд.</p>"""
    return page("Состояние бота", "status", body, refresh=30)


async def health(request: web.Request) -> web.Response:
    checks = monitor.checks()
    ok = all(c["ok"] for c in checks)
    return web.json_response({"ok": ok, "checks": checks}, status=200 if ok else 503)


async def root(request: web.Request) -> web.Response:
    raise web.HTTPFound("/admin")


def create_app(cfg: Config) -> web.Application:
    app = web.Application(middlewares=[make_auth(cfg)])
    app.router.add_get("/", root)
    app.router.add_get("/admin", admin)
    app.router.add_get("/admin/users.csv", users_csv)
    app.router.add_get("/status", status)
    app.router.add_get("/health", health)
    return app
