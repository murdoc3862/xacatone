"""Веб-панель: /admin — зарегистрированные пользователи, /status — проверка работы бота,
/health — JSON для внешнего мониторинга (без пароля)."""
from __future__ import annotations

import base64
import mimetypes
import csv
import hmac
import json
import io
import time
from datetime import datetime
from html import escape

from aiogram.exceptions import TelegramAPIError, TelegramForbiddenError
from aiohttp import web

from urllib.parse import quote, urlparse

from . import db, monitor, quiz
from .quizzes import BY_ID, QUIZZES
from .config import Config

WEBHOOK_PATH = "/tg/webhook"

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
h2{font-size:16px;margin:24px 0 10px}a.btn{color:var(--accent)}button{font:inherit;padding:7px 14px;border-radius:8px;border:1px solid var(--accent);background:var(--accent);color:#fff;cursor:pointer}
button.stop{background:var(--bad);border-color:var(--bad)}button:disabled{opacity:.4;cursor:not-allowed}
form{display:inline;margin:0}details summary{cursor:pointer;color:var(--muted)}ol{margin:6px 0 0;padding-left:20px}
.badge{display:inline-block;min-width:18px;padding:0 6px;border-radius:9px;background:var(--bad);color:#fff;font-size:12px;font-weight:700;text-align:center;margin-left:4px}
.chat{display:flex;flex-direction:column;gap:8px;background:var(--card);border:1px solid var(--line);border-radius:10px;padding:16px;height:min(60vh,560px);overflow-y:auto}
.msg{max-width:75%;padding:8px 12px;border-radius:12px;white-space:pre-wrap;word-wrap:break-word}
.msg.in{align-self:flex-start;background:var(--bg);border:1px solid var(--line)}
.msg.out{align-self:flex-end;background:var(--accent);color:#fff}
.msg .t{font-size:11px;opacity:.7;margin-top:3px}.msg img{max-width:260px;border-radius:8px;display:block}
.msg.out a{color:#fff}.send{display:flex;gap:8px;margin-top:10px}
.send textarea{flex:1;font:inherit;padding:9px 12px;border:1px solid var(--line);border-radius:8px;background:var(--card);color:var(--text);resize:vertical;min-height:44px}
tr.unread td{font-weight:600}a.row{color:inherit;text-decoration:none}
.flash{padding:10px 14px;border-radius:8px;margin-bottom:16px;border:1px solid var(--bad);color:var(--bad)}
input{padding:7px 10px;border:1px solid var(--line);border-radius:8px;background:var(--card);color:var(--text);width:260px;max-width:100%}
</style>"""


def page(title: str, active: str, body: str, refresh: int | None = None) -> web.Response:
    meta = f'<meta http-equiv="refresh" content="{refresh}">' if refresh else ""
    try:
        unread = db.unread_total()
    except Exception:  # noqa: BLE001 — страница состояния должна открываться даже при сбое БД
        unread = 0
    badge = f'<span class="badge" id="unread">{unread}</span>' if unread else ""
    nav = "".join(
        f'<a href="{href}" class="{"on" if key == active else ""}">{label}</a>'
        for key, href, label in [("admin", "/admin", "Пользователи"), ("chats", "/chats", "Сообщения" + badge),
                                 ("quizzes", "/quizzes", "Тесты"), ("status", "/status", "Состояние бота")]
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
        if request.path in ("/health", WEBHOOK_PATH):
            return await handler(request)
        got = request.headers.get("Authorization", "")
        if not hmac.compare_digest(got.encode(), expected.encode()):
            return web.Response(status=401, text="Нужен логин и пароль",
                                headers={"WWW-Authenticate": 'Basic realm="admin", charset="UTF-8"'})
        return await handler(request)

    return auth


async def admin(request: web.Request) -> web.Response:
    users = db.list_users()
    totals = db.user_quiz_totals()
    s = db.stats()
    rows = []
    for i, u in enumerate(users, 1):
        uname = f'<a class="btn" href="https://t.me/{escape(u["username"])}" target="_blank">@{escape(u["username"])}</a>' if u["username"] else "<span class='muted'>—</span>"
        tg_name = escape(" ".join(x for x in (u["first_name"], u["last_name"]) if x))
        name = escape(u["name"]) if u["name"] else "<span class='muted'>ещё не ввёл имя</span>"
        name = f"<a class='btn' href='/chats/{u['tg_id']}' title='Написать'>{name}</a>"
        rows.append(
            f"<tr><td>{i}</td><td><b>{name}</b></td><td>{uname}</td><td>{tg_name}</td>"
            f"<td class='muted'>{u['tg_id']}</td><td>{fmt_totals(totals.get(u['tg_id']))}</td>"
            f"<td>{u['registered_at'] or '—'}</td><td class='muted'>{u['started_at']}</td></tr>")
    table = "".join(rows) or "<tr><td colspan='8' class='muted'>Пока никто не зарегистрировался</td></tr>"
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
      <th>Telegram ID</th><th>Тесты</th><th>Зарегистрирован</th><th>Первый /start</th></tr></thead><tbody>{table}</tbody></table></div>
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
    labels = {"start": "/start", "registered": "Регистрация", "error": "Ошибка",
              "quiz_start": "Запуск теста", "quiz_stop": "Остановка теста"}
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


def fmt_totals(t) -> str:
    if not t:
        return "<span class='muted'>—</span>"
    return f"{t['score']} из {t['answered']} <span class='muted'>({t['runs']} тест.)</span>"


def same_origin(request: web.Request) -> bool:
    """Защита от CSRF: POST принимаем только со страниц самой панели."""
    src = request.headers.get("Origin") or request.headers.get("Referer")
    return bool(src) and urlparse(src).netloc == request.host


def fmt_dt(ts: str | None) -> str:
    return ts[:19] if ts else "—"


def fmt_avg(v) -> str:
    return "—" if v is None else f"{v:.1f} из 5"


def fmt_secs(sec) -> str:
    if sec is None:
        return "—"
    m, s_ = divmod(int(sec), 60)
    return f"{m} мин {s_} с" if m else f"{s_} с"


async def quizzes_page(request: web.Request) -> web.Response:
    active = db.active_run()
    runs = db.list_runs()
    times_run = {}
    for r_ in runs:
        times_run[r_["quiz_id"]] = times_run.get(r_["quiz_id"], 0) + 1
    err = request.query.get("err")
    body = f"<div class='flash'>{escape(err)}</div>" if err else ""

    if active:
        a = next(r_ for r_ in runs if r_["id"] == active["id"])
        qz = BY_ID[active["quiz_id"]]
        body += f"""<div class="banner"><span class="dot ok"></span>Идёт тест: «{escape(qz['title'])}» —
          запущен {fmt_dt(active['started_at'])}
          <form method="post" action="/quizzes/stop" style="float:right"
                onsubmit="return confirm('Остановить тест и разослать всем места?')">
            <input type="hidden" name="run_id" value="{active['id']}"><button class="stop">Остановить тест</button></form></div>
        <div class="cards">
          <div class="card"><div class="v">{active['sent_count']}</div><div class="l">Получили тест</div></div>
          <div class="card"><div class="v">{a['answered']}</div><div class="l">Начали отвечать</div></div>
          <div class="card"><div class="v">{a['finished']}</div><div class="l">Ответили на все вопросы</div></div>
          <div class="card"><div class="v {'bad' if active['fail_count'] else ''}">{active['fail_count']}</div><div class="l">Не доставлено</div></div>
        </div>
        <p><a class="btn" href="/quizzes/run/{active['id']}">Текущие результаты →</a>
           <span class="muted"> · страница обновляется каждые 15 секунд</span></p>"""

    rows = []
    for qz in QUIZZES:
        qs = "".join(f"<li>{escape(q['q'])} <span class='ok'>({escape(q['options'][q['correct']])})</span></li>"
                     for q in qz["questions"])
        dis = "disabled title='Сначала остановите текущий тест'" if active else ""
        rows.append(f"""<tr><td>{qz['id']}</td><td><b>{escape(qz['title'])}</b>
          <details><summary>Вопросы и ответы</summary><ol>{qs}</ol></details></td>
          <td>{times_run.get(qz['id'], 0)}</td>
          <td><form method="post" action="/quizzes/start"
                onsubmit="return confirm('Разослать тест «{escape(qz['title'])}» всем зарегистрированным?')">
            <input type="hidden" name="quiz_id" value="{qz['id']}"><button {dis}>Запустить</button></form></td></tr>""")
    body += f"""<h2>Тесты по истории России</h2><div class="wrap"><table>
      <thead><tr><th>#</th><th>Тест</th><th>Запусков</th><th></th></tr></thead><tbody>{''.join(rows)}</tbody></table></div>"""

    hist = "".join(
        f"<tr><td>{r_['id']}</td><td>{escape(BY_ID[r_['quiz_id']]['title'])}</td>"
        f"<td>{fmt_dt(r_['started_at'])}</td><td>{fmt_dt(r_['stopped_at']) if r_['stopped_at'] else '<b class=ok>идёт</b>'}</td>"
        f"<td>{r_['sent_count']}</td><td>{r_['answered']}</td>"
        f"<td>{fmt_avg(r_['avg_score'])}</td>"
        f"<td><a class='btn' href='/quizzes/run/{r_['id']}'>Результаты</a></td></tr>"
        for r_ in runs) or "<tr><td colspan='8' class='muted'>Тесты ещё не запускались</td></tr>"
    body += f"""<h2>История запусков</h2><div class="wrap"><table>
      <thead><tr><th>#</th><th>Тест</th><th>Запущен</th><th>Остановлен</th><th>Получили</th>
      <th>Ответили</th><th>Средний балл</th><th></th></tr></thead><tbody>{hist}</tbody></table></div>"""
    return page("Тесты", "quizzes", body, refresh=15 if active else None)


async def run_page(request: web.Request) -> web.Response:
    run = db.get_run(int(request.match_info["run_id"]))
    if run is None:
        raise web.HTTPNotFound()
    qz = BY_ID[run["quiz_id"]]
    n = len(qz["questions"])
    ranked = db.ranking(run["id"])
    parts = {p["tg_id"]: p for p in db.run_participants(run["id"])}
    answers: dict[int, dict[int, dict]] = {}
    for a in db.run_answers(run["id"]):
        answers.setdefault(a["tg_id"], {})[a["q_index"]] = a

    head_q = "".join(f"<th title='{escape(q['q'])}'>В{i + 1}</th>" for i, q in enumerate(qz["questions"]))
    rows = []
    for place, p in enumerate(ranked, 1):
        cells = ""
        for i, q in enumerate(qz["questions"]):
            a = answers.get(p["tg_id"], {}).get(i)
            if a is None:
                cells += "<td class='muted'>·</td>"
            else:
                tip = escape(q["options"][a["chosen"]])
                cells += f"<td title='{tip}'>{'✅' if a['is_correct'] else '❌'}</td>"
        uname = f"@{escape(p['username'])}" if p["username"] else ""
        rows.append(f"<tr><td><b>{place}</b></td><td>{escape(p['name'] or '—')} <span class='muted'>{uname}</span></td>"
                    f"<td><b>{p['score']}</b> из {n}</td>{cells}<td class='muted'>{fmt_secs(p['seconds'])}</td></tr>")
    waiting = sum(1 for p in parts.values() if p["current_q"] == 0)
    table = "".join(rows) or f"<tr><td colspan='{n + 4}' class='muted'>Пока никто не ответил</td></tr>"

    stat = []
    for i, q in enumerate(qz["questions"]):
        got = [a for per in answers.values() for j, a in per.items() if j == i]
        right = sum(a["is_correct"] for a in got)
        pct = f"{round(right / len(got) * 100)}%" if got else "—"
        stat.append(f"<tr><td>В{i + 1}</td><td>{escape(q['q'])}<br><span class='ok'>{escape(q['options'][q['correct']])}</span></td>"
                    f"<td>{right} из {len(got)}</td><td><b>{pct}</b></td></tr>")

    status = ("<b class='ok'>идёт</b> — места предварительные" if run["status"] == "active"
              else f"остановлен {fmt_dt(run['stopped_at'])} — места разосланы участникам")
    body = f"""<p><a class="btn" href="/quizzes">← Все тесты</a></p>
    <h2 style="margin-top:0">«{escape(qz['title'])}» · запуск #{run['id']}</h2>
    <p class="muted">Запущен {fmt_dt(run['started_at'])} · {status}</p>
    <div class="cards">
      <div class="card"><div class="v">{run['sent_count']}</div><div class="l">Получили тест</div></div>
      <div class="card"><div class="v">{len(ranked)}</div><div class="l">Ответили</div></div>
      <div class="card"><div class="v">{waiting}</div><div class="l">Ещё не начали</div></div>
    </div>
    <h2>Рейтинг</h2><p class="muted">Место: больше правильных → больше отвеченных → быстрее.</p>
    <div class="wrap"><table><thead><tr><th>Место</th><th>Участник</th><th>Баллы</th>{head_q}<th>Время</th></tr></thead>
      <tbody>{table}</tbody></table></div>
    <h2>По вопросам</h2><div class="wrap"><table><thead><tr><th></th><th>Вопрос и верный ответ</th><th>Верно</th><th>%</th></tr></thead>
      <tbody>{''.join(stat)}</tbody></table></div>"""
    return page(f"Тест #{run['id']}", "quizzes", body, refresh=15 if run["status"] == "active" else None)


async def quiz_start(request: web.Request) -> web.Response:
    if not same_origin(request):
        raise web.HTTPForbidden(text="Неверный источник запроса")
    form = await request.post()
    try:
        quiz.start(request.app["bot"], int(form.get("quiz_id", 0)))
    except (RuntimeError, ValueError) as e:
        raise web.HTTPSeeOther("/quizzes?err=" + quote(str(e)))
    raise web.HTTPSeeOther("/quizzes")


async def quiz_stop(request: web.Request) -> web.Response:
    if not same_origin(request):
        raise web.HTTPForbidden(text="Неверный источник запроса")
    form = await request.post()
    run_id = int(form.get("run_id", 0))
    try:
        quiz.stop(request.app["bot"], run_id)
    except (RuntimeError, ValueError) as e:
        raise web.HTTPSeeOther("/quizzes?err=" + quote(str(e)))
    raise web.HTTPSeeOther(f"/quizzes/run/{run_id}")


MEDIA_LABELS = {"photo": "Фото", "voice": "Голосовое", "video": "Видео", "document": "Файл", "audio": "Аудио",
                "video_note": "Видеосообщение", "animation": "GIF", "sticker": "Стикер"}


def badge_html(n) -> str:
    return f"<span class='badge'>{n}</span>" if n else ""


def preview(text, media) -> str:
    label = f"[{MEDIA_LABELS.get(media, media)}] " if media else ""
    t = (text or "").replace("\n", " ")
    return escape(label + (t[:80] + "…" if len(t) > 80 else t))


async def chats_page(request: web.Request) -> web.Response:
    convs = db.conversations()
    rows = "".join(
        f"<tr class='{'unread' if c['unread'] else ''}'>"
        f"<td><a class='btn' href='/chats/{c['tg_id']}'>{escape(c['name'] or c['first_name'] or str(c['tg_id']))}</a>"
        f"{(' <span class=muted>@' + escape(c['username']) + '</span>') if c['username'] else ''}</td>"
        f"<td>{'<span class=muted>Вы: </span>' if c['last_dir'] == 'out' else ''}{preview(c['last_text'], c['last_media'])}</td>"
        f"<td class='muted'>{c['last_ts']}</td>"
        f"<td>{badge_html(c['unread'])}</td></tr>"
        for c in convs) or "<tr><td colspan='4' class='muted'>Сообщений пока нет. Написать первым можно со страницы «Пользователи» — нажмите на имя.</td></tr>"
    body = f"""<h2 style="margin-top:0">Сообщения от подписчиков</h2>
    <p class="muted">Всё, что подписчики пишут боту, попадает сюда. Нажмите на имя, чтобы открыть переписку и ответить.</p>
    <div class="wrap"><table><thead><tr><th>Подписчик</th><th>Последнее сообщение</th><th>Когда</th><th></th></tr></thead>
    <tbody>{rows}</tbody></table></div>"""
    return page("Сообщения", "chats", body, refresh=20)


def msg_json(m) -> dict:
    return {"id": m["id"], "dir": m["direction"], "text": m["text"] or "", "ts": m["ts"],
            "media": m["media_type"], "media_label": MEDIA_LABELS.get(m["media_type"], m["media_type"]),
            "file": bool(m["file_id"])}


async def chat_page(request: web.Request) -> web.Response:
    tg_id = int(request.match_info["tg_id"])
    u = db.get_user(tg_id)
    if u is None:
        raise web.HTTPNotFound(text="Нет такого подписчика")
    db.mark_read(tg_id)
    msgs = [msg_json(m) for m in db.thread(tg_id)]
    title = escape(u["name"] or u["first_name"] or str(tg_id))
    uname = f" · <a class='btn' href='https://t.me/{escape(u['username'])}' target='_blank'>@{escape(u['username'])}</a>" if u["username"] else ""
    data = json.dumps(msgs, ensure_ascii=False).replace("<", "\\u003c")
    body = f"""<p><a class="btn" href="/chats">← Все переписки</a></p>
    <h2 style="margin-top:0">{title}</h2>
    <p class="muted">Telegram ID {tg_id}{uname} · зарегистрирован {u['registered_at'] or '—'}</p>
    <div class="chat" id="chat"></div>
    <form class="send" id="f"><textarea id="t" placeholder="Сообщение… (Enter — отправить, Shift+Enter — новая строка)" maxlength="4000"></textarea>
      <button id="b">Отправить</button></form>
    <p class="flash" id="err" style="display:none"></p>
    <script>
    const ID={tg_id}; let last=0; const chat=document.getElementById('chat');
    function esc(s){{return s.replace(/[&<>"']/g,c=>({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}})[c])}}
    function add(m){{ if(m.id<=last) return; last=m.id;
      const d=document.createElement('div'); d.className='msg '+m.dir; let h='';
      if(m.media==='photo'&&m.file) h+='<a href="/chats/file/'+m.id+'" target="_blank"><img src="/chats/file/'+m.id+'" loading="lazy"></a>';
      else if(m.media) h+=(m.file?'<a href="/chats/file/'+m.id+'" target="_blank">':'')+'['+esc(m.media_label||'')+']'+(m.file?'</a>':'')+(m.text?' ':'');
      h+=esc(m.text)+'<div class="t">'+esc(m.ts)+'</div>'; d.innerHTML=h;
      const bottom=chat.scrollHeight-chat.scrollTop-chat.clientHeight<60; chat.appendChild(d); if(bottom) chat.scrollTop=chat.scrollHeight; }}
    {data}.forEach(add); chat.scrollTop=chat.scrollHeight;
    if(!last) chat.innerHTML='<p class="muted" id="empty">Переписки ещё нет — напишите первым.</p>';
    async function poll(){{ try{{ const r=await fetch('/chats/'+ID+'/messages.json?after='+last); if(r.ok){{ const ms=await r.json();
      if(ms.length) document.getElementById('empty')?.remove(); ms.forEach(add); }} }}catch(e){{}} }}
    setInterval(poll,4000);
    const t=document.getElementById('t'), b=document.getElementById('b'), err=document.getElementById('err');
    t.addEventListener('keydown',e=>{{ if(e.key==='Enter'&&!e.shiftKey){{ e.preventDefault(); document.getElementById('f').requestSubmit(); }} }});
    document.getElementById('f').addEventListener('submit', async e=>{{ e.preventDefault(); const text=t.value.trim(); if(!text) return;
      b.disabled=true; err.style.display='none';
      try{{ const r=await fetch('/chats/'+ID+'/send',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{text}})}});
        const j=await r.json(); if(!r.ok) throw new Error(j.error||'Ошибка'); t.value=''; document.getElementById('empty')?.remove(); add(j.message);
      }}catch(x){{ err.textContent='Не отправлено: '+x.message; err.style.display='block'; }} finally{{ b.disabled=false; t.focus(); }} }});
    </script>"""
    return page(f"Переписка: {title}", "chats", body)


async def chat_messages(request: web.Request) -> web.Response:
    tg_id = int(request.match_info["tg_id"])
    after = int(request.query.get("after", "0") or 0)
    msgs = db.thread(tg_id, after)
    if any(m["direction"] == "in" for m in msgs):
        db.mark_read(tg_id)
    return web.json_response([msg_json(m) for m in msgs])


async def chat_send(request: web.Request) -> web.Response:
    if not same_origin(request):
        return web.json_response({"error": "Неверный источник запроса"}, status=403)
    tg_id = int(request.match_info["tg_id"])
    if db.get_user(tg_id) is None:
        return web.json_response({"error": "Нет такого подписчика"}, status=404)
    data = await request.json()
    text = str(data.get("text", "")).strip()
    if not text or len(text) > 4000:
        return web.json_response({"error": "Сообщение пустое или длиннее 4000 символов"}, status=400)
    try:
        await request.app["bot"].send_message(tg_id, text)
    except TelegramForbiddenError:
        return web.json_response({"error": "подписчик заблокировал бота"}, status=400)
    except TelegramAPIError as e:
        return web.json_response({"error": str(e)}, status=400)
    msg_id = db.add_message(tg_id, "out", text)
    return web.json_response({"message": msg_json(db.get_message(msg_id))})


async def chat_file(request: web.Request) -> web.Response:
    m = db.get_message(int(request.match_info["msg_id"]))
    if m is None or not m["file_id"]:
        raise web.HTTPNotFound()
    bot = request.app["bot"]
    try:
        f = await bot.get_file(m["file_id"])
        data = await bot.download_file(f.file_path)
    except TelegramAPIError as e:
        raise web.HTTPNotFound(text=f"Файл недоступен: {e}")
    ctype = mimetypes.guess_type(f.file_path or "")[0] or "application/octet-stream"
    return web.Response(body=data.read(), content_type=ctype, headers={"Cache-Control": "private, max-age=86400"})


async def health(request: web.Request) -> web.Response:
    checks = monitor.checks()
    ok = all(c["ok"] for c in checks)
    return web.json_response({"ok": ok, "checks": checks}, status=200 if ok else 503)


async def root(request: web.Request) -> web.Response:
    raise web.HTTPFound("/admin")


def create_app(cfg: Config, bot=None) -> web.Application:
    app = web.Application(middlewares=[make_auth(cfg)])
    app["bot"] = bot
    app.router.add_get("/", root)
    app.router.add_get("/admin", admin)
    app.router.add_get("/admin/users.csv", users_csv)
    app.router.add_get("/chats", chats_page)
    app.router.add_get(r"/chats/{tg_id:\d+}", chat_page)
    app.router.add_get(r"/chats/{tg_id:\d+}/messages.json", chat_messages)
    app.router.add_post(r"/chats/{tg_id:\d+}/send", chat_send)
    app.router.add_get(r"/chats/file/{msg_id:\d+}", chat_file)
    app.router.add_get("/quizzes", quizzes_page)
    app.router.add_post("/quizzes/start", quiz_start)
    app.router.add_post("/quizzes/stop", quiz_stop)
    app.router.add_get(r"/quizzes/run/{run_id:\d+}", run_page)
    app.router.add_get("/status", status)
    app.router.add_get("/health", health)
    return app
