#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pre_tool_call-хук: не даёт Гере молча делать работу профиля своими руками.

Ставится в `hooks:` конфига Геры и НИКОГДА не ставится профилям — внутри профиля
та же работа законна.

Почему именно так:

* Блокируется **узкий сигнал, а не инструмент.** `terminal` и `execute_code`
  у Геры остаются: у `delegate_task` дети наследуют тулсеты родителя, срежем
  руки Гере — срежем и детям (инвариант 3 из CLAUDE.md).
* Сигнал выбран по замеру, а не на глаз: в обоих контрольных прогонах
  27.08.2026 **первым же вызовом** шёл `skill_view business-documents`, дальше
  13 и 20 вызовов своими руками. Ловить надо там — до того, как он начал.
* Второй сигнал — та же работа в обход навыка (`python-docx` руками). После
  этапа 19.4 документные навыки у Геры выключены, и это станет основным путём.
* Отказ фейл-опен: любая ошибка внутри хука = пропустить вызов. Сломанный
  сторож не имеет права остановить агента клиента.

Карта зон — `profile_guard.json` рядом с этим файлом. Код при добавлении
профиля не трогается.
"""

import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG = os.path.join(HERE, "profile_guard.json")

# Поля, в которых у соответствующего тула лежит то, что он собирается сделать.
TEXT_FIELDS = {
    "terminal": ("command",),
    "execute_code": ("code", "command"),
    "write_file": ("path", "content"),
    "patch": ("path", "content", "patch"),
}

# Команды чтения и переноса: готовый файл профиля надо уметь посмотреть и
# переслать, это не производство документа.
ALLOW_PREFIX = re.compile(
    r"^\s*(ls|cat|head|tail|file|stat|du|find|cp|mv|base64|md5sum|wc)\b")

# Путь навыка в команде — тот же полез-делать-сам, только через скрипт навыка
# (замерено: `python3 .../business-documents/scripts/validate_ru_requisites.py`).
SKILL_PATH = "skills/"


def emit(obj):
    if obj:
        # Пишем байтами: текст отказа кириллический, а локаль процесса-хозяина
        # не гарантирована. sys.stdout.write отдал бы его в cp1251 и hermes
        # получил бы мусор вместо инструкции.
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        getattr(sys.stdout, "buffer", sys.stdout).write(data)
    sys.exit(0)


def log(cfg, record):
    path = cfg.get("log")
    if not path:
        return
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        record["ts"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        pass  # журнал не повод ронять решение


def refusal(name, zone):
    return (
        u"Это зона профиля `%s` (%s). Делать самому нельзя: у профиля свои "
        u"образцы, реквизиты и критик, который пересчитывает суммы, — у тебя "
        u"этого нет.\n\n"
        u"Передай задачу одним вызовом:\n"
        u"kanban_create(title=\"...\", assignee=\"%s\", body=\"<самодостаточное "
        u"ТЗ: реквизиты, суммы, позиции, чем считать готовым>\", "
        u"max_runtime_seconds=1800)\n\n"
        u"Профиль работает без диалога и ничего не сможет переспросить — всё, "
        u"что знаешь, перенеси в body. Мике ответь сразу одной строкой: "
        u"«Передал в %s, задача t_..., вернусь с результатом», и продолжай "
        u"разговор, не дожидаясь. Подробности — навык `meta/profile-delegate`."
        % (name, zone, name, name)
    )


def main():
    payload = json.loads(sys.stdin.read() or "{}")
    tool = payload.get("tool_name") or ""
    args = payload.get("tool_input") or {}

    # Внутри воркера kanban эта же работа — и есть задача. Гера воркером не
    # бывает, но проверка стоит копейки и снимает целый класс ошибок.
    if os.environ.get("HERMES_KANBAN_TASK"):
        emit(None)

    with open(CONFIG, encoding="utf-8") as fh:
        cfg = json.load(fh)

    profiles = [(n, p) for n, p in (cfg.get("profiles") or {}).items()
                if p.get("enabled")]
    if not profiles:
        emit(None)

    # Правило A: открыл навык из чужой зоны.
    if tool == "skill_view":
        skill = str(args.get("name") or "").strip()
        for name, prof in profiles:
            if skill in (prof.get("skills") or []):
                log(cfg, {"rule": "skill", "tool": tool, "profile": name,
                          "detail": skill, "session": payload.get("session_id")})
                emit({"action": "block",
                      "message": refusal(name, prof.get("what") or name)})
        emit(None)

    # Правило B: та же работа в обход навыка.
    fields = TEXT_FIELDS.get(tool)
    if not fields:
        emit(None)

    text = "\n".join(str(args.get(f) or "") for f in fields)
    if not text.strip():
        emit(None)

    if tool in ("terminal", "execute_code") and ALLOW_PREFIX.match(text):
        emit(None)

    low = text.lower()
    for name, prof in profiles:
        for skill in (prof.get("skills") or []):
            if SKILL_PATH + skill in low or "/" + skill + "/" in low:
                log(cfg, {"rule": "skill-path", "tool": tool, "profile": name,
                          "detail": skill, "session": payload.get("session_id")})
                emit({"action": "block",
                      "message": refusal(name, prof.get("what") or name)})
        for marker in (prof.get("markers") or []):
            if marker.lower() in low:
                log(cfg, {"rule": "marker", "tool": tool, "profile": name,
                          "detail": marker, "session": payload.get("session_id")})
                emit({"action": "block",
                      "message": refusal(name, prof.get("what") or name)})

    emit(None)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # фейл-опен: сторож не роняет агента
        sys.stderr.write("profile_guard: %s\n" % exc)
        sys.exit(0)
