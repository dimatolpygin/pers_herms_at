#!/usr/bin/env python3
"""Google Calendar через сервисный аккаунт: чтение, создание, правка, удаление.

Почему свой скрипт, а не штатный `google-workspace`:
  1. в штатном `google_api.py` нет команды правки события (только list/create/delete);
  2. он умеет только OAuth — сервисные аккаунты не поддержаны (grep service_account пуст);
  3. он лежит в /usr/local/lib/hermes-agent и ПЕРЕЗАПИСЫВАЕТСЯ обновлением hermes,
     поэтому патчить его нельзя — правка молча исчезнет.

Сервисный аккаунт удобнее OAuth на сервере: не истекает и не требует браузера.
Но у него НЕТ своего `primary` — он работает только с календарями, которые ему
расшарили с правом «Внесение изменений в мероприятия».

  python gcal.py calendars
  python gcal.py list [--from 2026-08-06] [--to 2026-08-13] [--calendar ID]
  python gcal.py create --summary "Встреча" --start 2026-08-07T15:00 --end 2026-08-07T16:00
  python gcal.py update EVENT_ID [--summary ...] [--start ...] [--end ...]
  python gcal.py delete EVENT_ID

Время без таймзоны считается московским. Вывод — всегда JSON.

Переменные окружения:
  GOOGLE_SERVICE_ACCOUNT_FILE  путь к JSON сервисного аккаунта (обязателен)
  GOOGLE_CALENDAR_ID           календарь по умолчанию (иначе --calendar)
  HERMES_ENV_FILE              необязательный явный путь к .env
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

MSK = timezone(timedelta(hours=3))
SCOPES = ["https://www.googleapis.com/auth/calendar"]

# Интерпретаторы, в которых уже стоят google-библиотеки (venv hermes — первый).
_CANDIDATE_PYTHONS = [
    "/usr/local/lib/hermes-agent/venv/bin/python",
    "/usr/local/lib/hermes-agent/venv/bin/python3",
]


def _fail(message: str, hint: str = "") -> None:
    """Честная ошибка машинно-читаемым JSON — без выдумок (правило этапа 8)."""
    out = {"ok": False, "error": message}
    if hint:
        out["hint"] = hint
    print(json.dumps(out, ensure_ascii=False, indent=2))
    sys.exit(1)


def _reexec_with_google_libs() -> None:
    """google-библиотеки живут в venv hermes. Если текущего python им не хватает —
    перезапускаем себя тем, у кого они есть. Иначе скрипт нельзя было бы звать
    обычным `python` из песочницы агента."""
    for candidate in _CANDIDATE_PYTHONS:
        if not Path(candidate).is_file() or os.path.realpath(candidate) == os.path.realpath(sys.executable):
            continue
        os.execv(candidate, [candidate, os.path.abspath(__file__)] + sys.argv[1:])
    _fail(
        "не найдены библиотеки google-auth / google-api-python-client",
        "поставить: /usr/local/lib/hermes-agent/venv/bin/pip install google-api-python-client google-auth",
    )


try:
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError
except ImportError:
    _reexec_with_google_libs()


def load_env_file() -> None:
    """Подхватить KEY=VALUE из .env Hermes. Существующее окружение не трогаем."""
    candidates = []
    explicit = os.environ.get("HERMES_ENV_FILE")
    if explicit:
        candidates.append(Path(explicit))
    home = os.environ.get("HERMES_HOME")
    if home:
        candidates.append(Path(home) / ".env")
    local = os.environ.get("LOCALAPPDATA")
    if local:
        candidates.append(Path(local) / "hermes" / ".env")
    candidates.append(Path.home() / ".hermes" / ".env")

    for path in candidates:
        if not path.is_file():
            continue
        for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def get_service():
    sa_file = os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE", "").strip()
    if not sa_file:
        _fail("не задан GOOGLE_SERVICE_ACCOUNT_FILE",
              "путь к JSON сервисного аккаунта — в .env Hermes")
    if not Path(sa_file).is_file():
        _fail("файл сервисного аккаунта не найден: %s" % sa_file)
    try:
        creds = service_account.Credentials.from_service_account_file(sa_file, scopes=SCOPES)
    except Exception as exc:
        _fail("не читается ключ сервисного аккаунта: %s" % exc)
    return build("calendar", "v3", credentials=creds, cache_discovery=False)


def calendar_id(args) -> str:
    return args.calendar or os.environ.get("GOOGLE_CALENDAR_ID", "").strip() or "primary"


def parse_dt(value: str, *, end_of_day: bool = False) -> str:
    """Принимает `2026-08-07`, `2026-08-07T15:00` или полный ISO со смещением.
    Голое время считаем московским — агент живёт в МСК (этап 10)."""
    raw = value.strip()
    try:
        if len(raw) == 10:  # только дата
            dt = datetime.fromisoformat(raw)
            dt = dt.replace(hour=23, minute=59, second=59) if end_of_day else dt
        else:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        _fail("не понимаю дату/время: %r" % value,
              "форматы: 2026-08-07 | 2026-08-07T15:00 | 2026-08-07T15:00+03:00")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=MSK)
    return dt.isoformat()


def shorten(event: dict) -> dict:
    start = event.get("start", {})
    end = event.get("end", {})
    return {
        "id": event.get("id"),
        "summary": event.get("summary"),
        "start": start.get("dateTime") or start.get("date"),
        "end": end.get("dateTime") or end.get("date"),
        "location": event.get("location"),
        "description": event.get("description"),
        "link": event.get("htmlLink"),
    }


def explain_http_error(exc, cal: str) -> None:
    """Внятная причина вместо сырого стектрейса — иначе агент начнёт домысливать."""
    status = getattr(getattr(exc, "resp", None), "status", None)
    body = ""
    try:
        body = exc.content.decode("utf-8", "replace")
    except Exception:
        body = str(exc)

    if status == 403 and "has not been used in project" in body:
        _fail("Google Calendar API выключен в проекте",
              "владельцу проекта включить Calendar API в Google Cloud Console и подождать пару минут")
    if status in (403, 404) and cal != "primary":
        _fail("нет доступа к календарю %s (или он не существует)" % cal,
              "расшарить календарь на сервисный аккаунт с правом «Внесение изменений в мероприятия»")
    if status == 404:
        _fail("не найдено: событие или календарь")
    _fail("Google API вернул ошибку %s" % status, body[:300])


def cmd_calendars(args) -> None:
    svc = get_service()
    try:
        items = svc.calendarList().list().execute().get("items", [])
    except HttpError as exc:
        explain_http_error(exc, "primary")
    # Пустой список — НОРМА для сервисного аккаунта: расшаренный календарь не попадает
    # в его calendarList автоматически, но по прямому ID доступен. Пишем это прямо в
    # ответ, иначе агент читает пустоту как «доступа нет» и зря отказывается работать.
    note = None
    if not items:
        note = ("пусто — это НОРМАЛЬНО для сервисного аккаунта и НЕ означает отсутствие доступа. "
                "Работай с календарём из GOOGLE_CALENDAR_ID (%s) и проверяй доступ командой list."
                % (os.environ.get("GOOGLE_CALENDAR_ID") or "не задан"))
    print(json.dumps({
        "ok": True,
        "calendars": [{"id": c["id"], "summary": c.get("summary"), "access": c.get("accessRole")} for c in items],
        "note": note,
    }, ensure_ascii=False, indent=2))


def cmd_list(args) -> None:
    svc = get_service()
    cal = calendar_id(args)
    time_min = parse_dt(args.since) if args.since else datetime.now(MSK).isoformat()
    time_max = parse_dt(args.until, end_of_day=True) if args.until else (datetime.now(MSK) + timedelta(days=7)).isoformat()
    try:
        events = svc.events().list(
            calendarId=cal, timeMin=time_min, timeMax=time_max,
            singleEvents=True, orderBy="startTime", maxResults=args.max,
        ).execute().get("items", [])
    except HttpError as exc:
        explain_http_error(exc, cal)
    print(json.dumps({"ok": True, "calendar": cal, "from": time_min, "to": time_max,
                      "count": len(events), "events": [shorten(e) for e in events]},
                     ensure_ascii=False, indent=2))


def cmd_create(args) -> None:
    svc = get_service()
    cal = calendar_id(args)
    body = {
        "summary": args.summary,
        "start": {"dateTime": parse_dt(args.start), "timeZone": "Europe/Moscow"},
        "end": {"dateTime": parse_dt(args.end), "timeZone": "Europe/Moscow"},
    }
    if args.description:
        body["description"] = args.description
    if args.location:
        body["location"] = args.location
    if args.attendees:
        body["attendees"] = [{"email": e.strip()} for e in args.attendees.split(",") if e.strip()]
    try:
        event = svc.events().insert(calendarId=cal, body=body).execute()
    except HttpError as exc:
        explain_http_error(exc, cal)
    print(json.dumps({"ok": True, "action": "created", "event": shorten(event)}, ensure_ascii=False, indent=2))


def cmd_update(args) -> None:
    """Правка через patch: меняем только переданные поля, остальные не трогаем.
    Именно этого не хватало в штатном навыке."""
    svc = get_service()
    cal = calendar_id(args)
    body: dict = {}
    if args.summary:
        body["summary"] = args.summary
    if args.start:
        body["start"] = {"dateTime": parse_dt(args.start), "timeZone": "Europe/Moscow"}
    if args.end:
        body["end"] = {"dateTime": parse_dt(args.end), "timeZone": "Europe/Moscow"}
    if args.description is not None:
        body["description"] = args.description
    if args.location is not None:
        body["location"] = args.location
    if not body:
        _fail("нечего менять", "передай хотя бы одно из --summary/--start/--end/--description/--location")
    try:
        event = svc.events().patch(calendarId=cal, eventId=args.event_id, body=body).execute()
    except HttpError as exc:
        explain_http_error(exc, cal)
    print(json.dumps({"ok": True, "action": "updated", "changed": sorted(body.keys()),
                      "event": shorten(event)}, ensure_ascii=False, indent=2))


def cmd_delete(args) -> None:
    svc = get_service()
    cal = calendar_id(args)
    try:
        event = svc.events().get(calendarId=cal, eventId=args.event_id).execute()
        svc.events().delete(calendarId=cal, eventId=args.event_id).execute()
    except HttpError as exc:
        explain_http_error(exc, cal)
    print(json.dumps({"ok": True, "action": "deleted", "event": shorten(event)}, ensure_ascii=False, indent=2))


def main() -> None:
    load_env_file()
    parser = argparse.ArgumentParser(description="Google Calendar через сервисный аккаунт")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("calendars", help="какие календари доступны сервисному аккаунту")
    p.add_argument("--calendar")
    p.set_defaults(func=cmd_calendars)

    p = sub.add_parser("list", help="события за период (по умолчанию — ближайшая неделя)")
    p.add_argument("--from", dest="since")
    p.add_argument("--to", dest="until")
    p.add_argument("--calendar")
    p.add_argument("--max", type=int, default=50)
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("create", help="создать событие")
    p.add_argument("--summary", required=True)
    p.add_argument("--start", required=True)
    p.add_argument("--end", required=True)
    p.add_argument("--description")
    p.add_argument("--location")
    p.add_argument("--attendees", help="через запятую")
    p.add_argument("--calendar")
    p.set_defaults(func=cmd_create)

    p = sub.add_parser("update", help="изменить существующее событие")
    p.add_argument("event_id")
    p.add_argument("--summary")
    p.add_argument("--start")
    p.add_argument("--end")
    p.add_argument("--description")
    p.add_argument("--location")
    p.add_argument("--calendar")
    p.set_defaults(func=cmd_update)

    p = sub.add_parser("delete", help="удалить событие")
    p.add_argument("event_id")
    p.add_argument("--calendar")
    p.set_defaults(func=cmd_delete)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
