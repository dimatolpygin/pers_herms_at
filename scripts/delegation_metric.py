#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Метрика делегирования: сколько профильных ЗАДАЧ Гера отдал профилю, а сколько сделал сам.

Зачем. Жалоба клиента «Гера плохо делегирует» — это мнение. Приёмка этапа 19 требует числа:
из 10 профильных задач подряд делегировано не меньше 9. Один и тот же скрипт снимает
и baseline «как есть» (задним числом по реальной истории Telegram), и замер «после».
Иначе сравнивать не с чем.

Источник правды — /root/.hermes/state.db:
  sessions(id, source, user_id, ...)  — source='telegram' это диалог с Микой;
  messages(session_id, role, content, tool_calls, tool_name, timestamp, id).

## Единица счёта — задача, а не сообщение

Первый прогон считал по сообщениям и дал заведомо неверную картину. Реальная работа
клиента выглядит как разговор: «сделай КП в ворде» → «а ворд где?» → «файл ворд
пожалуйста» → «это 11-й вариант, ты потратил кучу моего времени». Это ОДНА задача из
десятка реплик, и половина реплик вообще без глагола — по отдельности они не ловятся.

Поэтому:
  * эпизод ОТКРЫВАЕТСЯ на сильном сигнале — однозначная фраза («коммерческое
    предложение») либо связка «признак области + глагол действия» («сделай КП в ворде»);
  * эпизод ПРОДОЛЖАЕТСЯ любой репликой с признаком области, если она пришла не позже
    --gap минут после предыдущей («а ворд где?»);
  * эпизод закрывается по паузе.

## Что считаем «делегировал»
  * вызов инструмента delegate_task;
  * запуск дочернего профиля: `hermes --profile <name>`;
  * навык-обёртка profile-delegate (появится на шаге 19.2);
  * постановка задачи профилю: `kanban assign ... <name>`.

Эпизод засчитан делегированным, если делегирование случилось хоть раз внутри него.

## Что в знаменателе
Только эпизоды, где работа РЕАЛЬНО делалась — руками Геры или профилем. Эпизоды, где
Гера отговорился текстом и ничего не создал, считаются отдельной строкой и в долю не
входят: там нечего было делегировать. Это делает цифру честной в обе стороны.

Классификация проверяема руками: каждый эпизод выгружается в отчёт с текстом реплик,
человек правит вердикты через --review. Автоматической «умной» классификации здесь нет
сознательно — цифру, которую нельзя перепроверить глазами, клиент справедливо не примет.

Примеры:
  # baseline по всей истории
  python3 delegation_metric.py --domain docs --out baseline.md --json baseline.json
  # замер «после»: последние 10 профильных задач
  python3 delegation_metric.py --domain docs --last 10 --out after.md
  # применить ручные правки вердиктов
  python3 delegation_metric.py --domain docs --review review.json --out final.md
"""

import argparse
import datetime as dt
import json
import os
import re
import sqlite3
import sys

DEFAULT_DB = "/root/.hermes/state.db"

# Машинная обвязка внутри сообщений с role='user'. Это НЕ слова клиента: пересказ
# картинки от vision, имя приложенного файла, служебные врезки движка, цитата
# предыдущей реплики самого агента. Почему это критично: в первом прогоне из 263
# кандидатов 42 оказались врезками [ASYNC…], 60 попали по описанию картинки,
# 41 — по имени приложенного файла. Считать это «задачами клиента» нельзя.
MACHINE_BLOCKS = [
    r"\[ASYNC DELEGATION BATCH COMPLETE.*?(?=\Z)",
    r"\[CONTEXT COMPACTION.*?(?=\Z)",
    r"\[Your active task list.*?(?=\Z)",
    r"\[The user sent an image~.*?(?=\Z)",
    r"\[The user sent an image but I couldn't quite see it.*?(?=\Z)",
    r"\[The user sent a (?:text )?document:.*?(?=\Z)",
    r"\[Image attached.*?\]",
    r"\[Replying to:\s*\".*?\"\]",
]

# Глагол действия. Отличает просьбу что-то сделать от разговора про документы
# («кто нам с КП помог в прошлый раз?»). Нужен только чтобы ОТКРЫТЬ эпизод —
# внутри уже открытого эпизода реплики без глагола засчитываются.
ACTION_RE = re.compile(
    r"сдела|подготов|состав|оформ|сформир|выстав|перевыстав|напиш|создай|создать|"
    r"сгенерир|посчитай|рассчита|пересчита|переделай|исправь|поправ|доработай|дополни|"
    r"обнови|заполни|собери|пришли|отправь|распечата|конвертир|запиши|замени|"
    r"добавь|убери|прикрепи",
    re.IGNORECASE)

DOMAINS = {
    "docs": {
        "title": "Документы (КП, счета, оферты, закрывающие, налоги)",
        # Настолько однозначны, что открывают эпизод без глагола.
        "phrases": [
            r"коммерческ\w*\s+предложен\w*",
            r"закрывающ\w*\s+документ\w*",
            r"сч[ёе]т[-\s]?оферт\w*",
            r"публичн\w*\s+оферт\w*",
            r"политик\w*\s+конфиденциальн\w*",
            r"обработк\w*\s+персональн\w*\s+данн\w*",
        ],
        # Признак области. Открывает эпизод только вместе с глаголом,
        # но продолжает уже открытый эпизод сам по себе.
        "nouns": [
            r"\bк\.?п\.?\b",
            r"\bсч[ёе]т\w*\b",
            r"\bинвойс\w*",
            r"\bоферт\w*",
            r"\bдоговор\w*",
            r"\bакт\b|\bакта\b|\bакты\b|\bактов\b|\bакте\b",
            r"\bнакладн\w*",
            r"\bреквизит\w*",
            r"\bdocx\b",
            r"\bворд\b|\bword\b",
            r"\bналог\w*",
            r"\bусн\b",
            r"\bсмет\w*|\bсмете\b",
            r"\bбланк\w*",
            r"\bспецификаци\w*",
            r"\bдокумент\b|\bдокумента\b|\bдокументы\b|\bдокументов\b|\bдокументе\b",
        ],
        "exclude": [
            # «насчёт» и «на счёт» — предлог «про», а не документ.
            r"насч[ёе]т\b",
            r"\bна\s+сч[ёе]т\b",
            # Деньги на балансе и счётчики аналитики — не документы.
            r"\bна\s+счету\b",
            r"сч[ёе]тчик\w*",
            r"сч[ёе]т\w*\s+(?:open\s?router|openrouter)",
            # «документация» — это не документ клиента.
            r"документаци\w*",
            r"\bдоговор\w*\s+(?:ились|имся|иться|ится)",
        ],
    },
    "dev": {
        "title": "Кодинг (скрипты, приложения, расширения)",
        "phrases": [],
        "nouns": [
            r"\bскрипт\w*", r"\bкод\b|\bкода\b|\bкоде\b", r"\bбаг\w*",
            r"\bдеплой\w*", r"\bрасширени\w*",
            r"\bpython\b|\bпитон\w*", r"\bapi\b",
        ],
        "exclude": [],
    },
}

# Инструменты, которыми Гера делает работу СВОИМИ руками.
WORK_TOOLS = {"write_file", "execute_code", "terminal", "patch", "process"}

# Признаки делегирования в аргументах вызовов.
DELEGATE_CMD_RE = re.compile(
    r"hermes\s+(?:--profile|-p)\s+(\w+)"
    r"|profile[-_]delegate"
    r"|kanban\s+assign\b",
    re.IGNORECASE)


def parse_args():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--db", default=DEFAULT_DB, help="путь к state.db")
    p.add_argument("--domain", default="docs", choices=sorted(DOMAINS),
                   help="предметная область профиля")
    p.add_argument("--source", default="telegram", help="источник сессий")
    p.add_argument("--user", default=None,
                   help="user_id клиента; по умолчанию — самый активный в этом источнике")
    p.add_argument("--since", default=None, help="дата с (ГГГГ-ММ-ДД)")
    p.add_argument("--until", default=None, help="дата по (ГГГГ-ММ-ДД), включительно")
    p.add_argument("--gap", type=int, default=45,
                   help="пауза в минутах, после которой начинается новая задача")
    p.add_argument("--drift", type=int, default=2,
                   help="сколько реплик не по теме подряд задача переживает, не закрываясь")
    p.add_argument("--last", type=int, default=None,
                   help="взять только последние N задач (для критерия «10 подряд»)")
    p.add_argument("--review", default=None,
                   help="JSON с правками вердиктов: {\"<id задачи>\": true|false}")
    p.add_argument("--out", default=None, help="файл отчёта (markdown); по умолчанию stdout")
    p.add_argument("--json", dest="json_out", default=None, help="выгрузка задач в JSON")
    p.add_argument("--quote", type=int, default=110, help="сколько символов реплики показывать")
    return p.parse_args()


def to_ts(s, end_of_day=False):
    if not s:
        return None
    d = dt.datetime.strptime(s, "%Y-%m-%d")
    if end_of_day:
        d = d.replace(hour=23, minute=59, second=59)
    return d.timestamp()


def pick_user(conn, source):
    """Самый активный собеседник в этом источнике — это и есть клиент."""
    row = conn.execute(
        "select user_id, count(*) n from sessions where source=? and user_id is not null "
        "group by user_id order by n desc limit 1", (source,)).fetchone()
    return row[0] if row else None


def clean(text):
    """Оставляем только слова самого клиента, вырезая машинную обвязку."""
    if not text:
        return ""
    for pat in MACHINE_BLOCKS:
        text = re.sub(pat, " ", text, flags=re.S | re.IGNORECASE)
    return re.sub(r"\s+", " ", text).strip()


def strip_traps(text, domain):
    low = (text or "").lower()
    for pat in DOMAINS[domain]["exclude"]:
        # Ловушку вырезаем, а не отбрасываем реплику целиком: рядом может стоять
        # настоящий признак («документацию дал, а теперь выстави счёт»).
        low = re.sub(pat, " ", low)
    return low


def signal(text, domain):
    """Насколько сильно реплика пахнет предметной областью.

    Возвращает 'strong' (можно открывать задачу), 'weak' (только продолжать
    уже открытую) или None.
    """
    low = strip_traps(text, domain)
    if not low:
        return None, []
    spec = DOMAINS[domain]
    phrases = [p for p in spec["phrases"] if re.search(p, low)]
    nouns = [p for p in spec["nouns"] if re.search(p, low)]
    if phrases:
        return "strong", phrases + nouns
    if nouns:
        return ("strong" if ACTION_RE.search(low) else "weak"), nouns
    return None, []


def read_turns(conn, source, user_id):
    """Разрезаем историю на ходы: реплика клиента + вся работа до следующей реплики."""
    sessions = [r[0] for r in conn.execute(
        "select id from sessions where source=? and (? is null or user_id=?) order by started_at",
        (source, user_id, user_id))]

    for sid in sessions:
        rows = conn.execute(
            "select id, role, content, tool_calls, timestamp from messages "
            "where session_id=? order by id", (sid,)).fetchall()
        turn = None
        out = []
        for r in rows:
            if r["role"] == "user":
                if turn:
                    out.append(turn)
                turn = {"session_id": sid, "msg_id": r["id"], "ts": r["timestamp"],
                        "text": clean(r["content"]), "tools": [], "cmds": []}
            elif turn is not None and r["tool_calls"]:
                try:
                    calls = json.loads(r["tool_calls"])
                except (ValueError, TypeError):
                    continue
                if isinstance(calls, dict):
                    calls = [calls]
                for call in calls:
                    fn = call.get("function") or {}
                    name = fn.get("name") or call.get("name")
                    if not name:
                        continue
                    turn["tools"].append(name)
                    args = fn.get("arguments") or call.get("arguments") or ""
                    if not isinstance(args, str):
                        args = json.dumps(args, ensure_ascii=False)
                    if args:
                        turn["cmds"].append(args)
        if turn:
            out.append(turn)
        yield sid, out


def delegated_in(turn):
    """Куда ушла работа хода. Два разных вида передачи, и путать их нельзя.

    'profile'  — работу забрал профиль-специалист: своя модель, свои навыки, свой
                 SOUL. Это то, чего требует критерий приёмки.
    'subagent' — delegate_task: ребёнок наследует тулсеты и модель родителя, то есть
                 это тот же Гера в другом окне, а не специалист. Считать это
                 «отдал профилю» было бы подтасовкой в свою пользу.
    """
    for args in turn["cmds"]:
        m = DELEGATE_CMD_RE.search(args)
        if m:
            return "profile", m.group(0)[:60]
    if "delegate_task" in turn["tools"]:
        return "subagent", "delegate_task"
    return None, None


def build_episodes(conn, a, user_id, ts_from, ts_to):
    """Склеиваем ходы в задачи.

    Задача продолжается, пока разговор о ней. Два ограничителя, оба нужны:
      * пауза больше --gap минут — человек ушёл, вернулся с другим;
      * больше --drift реплик подряд не по теме — разговор уехал в сторону.
    Без второго ограничителя один занятый день склеивается в «задачу» из сотни
    реплик, и внутрь попадают чужие делегирования — цифра завышается вдвое.
    """
    gap = a.gap * 60
    episodes = []
    stats = {"turns": 0, "machine": 0}

    for sid, turns in read_turns(conn, a.source, user_id):
        cur, drift = None, 0
        for t in turns:
            if ts_from and t["ts"] < ts_from:
                continue
            if ts_to and t["ts"] > ts_to:
                continue
            stats["turns"] += 1

            fits = cur is not None and (t["ts"] - cur["ts_end"] <= gap)

            if not t["text"]:
                # Служебная врезка, голое вложение или картинка без подписи — слов
                # клиента нет, но работа внутри такого хода относится к открытой
                # задаче. В сторону разговор при этом не уводит.
                stats["machine"] += 1
                if fits:
                    cur["turns"].append(t)
                    cur["ts_end"] = t["ts"]
                continue

            sig, hits = signal(t["text"], a.domain)

            if sig and fits:
                cur["turns"].append(t)
                cur["ts_end"] = t["ts"]
                cur["hits"].extend(hits)
                drift = 0
            elif sig == "strong":
                cur = {"session_id": sid, "ts_start": t["ts"], "ts_end": t["ts"],
                       "turns": [t], "hits": list(hits)}
                episodes.append(cur)
                drift = 0
            elif fits and drift < a.drift:
                # Реплика без признаков области, но разговор ещё о том же
                # («да», «давай», «ок») — работа по ней идёт в ту же задачу.
                cur["turns"].append(t)
                cur["ts_end"] = t["ts"]
                drift += 1
            else:
                cur, drift = None, 0

    for ep in episodes:
        ep["id"] = "%s:%d" % (dt.datetime.fromtimestamp(ep["ts_start"]).strftime("%Y%m%d-%H%M"),
                              ep["turns"][0]["msg_id"])
        marks = {"profile": [], "subagent": []}
        own = set()
        for t in ep["turns"]:
            kind, mark = delegated_in(t)
            if kind:
                marks[kind].append(mark)
            own |= {x for x in t["tools"] if x in WORK_TOOLS}
        ep["to_profile"] = bool(marks["profile"])
        ep["to_subagent"] = bool(marks["subagent"])
        ep["own_tools"] = sorted(own)
        if ep["to_profile"]:
            ep["how"] = ", ".join(sorted(set(marks["profile"])))
        elif ep["to_subagent"]:
            ep["how"] = "delegate_task (тот же профиль, не специалист)"
        elif own:
            ep["how"] = "сам: " + ", ".join(sorted(own))
        else:
            ep["how"] = "работы не было"
        ep["worked"] = ep["to_profile"] or ep["to_subagent"] or bool(own)
    return episodes, stats


def main():
    a = parse_args()
    if not os.path.exists(a.db):
        sys.exit("Не найдена база %s" % a.db)

    conn = sqlite3.connect(a.db)
    conn.row_factory = sqlite3.Row

    user_id = a.user or pick_user(conn, a.source)
    ts_from, ts_to = to_ts(a.since), to_ts(a.until, end_of_day=True)

    review = {}
    if a.review:
        with open(a.review, encoding="utf-8") as f:
            review = json.load(f)

    episodes, stats = build_episodes(conn, a, user_id, ts_from, ts_to)

    kept, dropped = [], 0
    for ep in episodes:
        verdict = review.get(ep["id"], True)
        if not verdict:
            dropped += 1
            continue
        ep["manual"] = ep["id"] in review
        kept.append(ep)

    kept.sort(key=lambda e: e["ts_start"])
    worked = [e for e in kept if e["worked"]]
    talk_only = len(kept) - len(worked)
    if a.last:
        worked = worked[-a.last:]

    n = len(worked)
    deleg = sum(1 for e in worked if e["to_profile"])
    sub = sum(1 for e in worked if e["to_subagent"] and not e["to_profile"])
    share = (100.0 * deleg / n) if n else 0.0

    lines = []
    w = lines.append
    w("# Метрика делегирования — профиль `%s`" % a.domain)
    w("")
    w("_%s_" % DOMAINS[a.domain]["title"])
    w("")
    w("**Снято**: %s" % dt.datetime.now().strftime("%Y-%m-%d %H:%M"))
    w("**База**: `%s` · источник `%s` · собеседник `%s`" % (a.db, a.source, user_id))
    period = "вся история"
    if a.since or a.until:
        period = "%s → %s" % (a.since or "начало", a.until or "сегодня")
    if a.last:
        period += " (последние %d задач)" % a.last
    w("**Период**: %s · пауза между задачами — %d мин" % (period, a.gap))
    w("")
    w("| Показатель | Значение |")
    w("|---|---:|")
    w("| Ходов в истории | %d |" % stats["turns"])
    w("| Из них служебных (врезки движка, вложения, картинки без подписи) | %d |" % stats["machine"])
    w("| **Профильных задач (`%s`)** | **%d** |" % (a.domain, len(kept)))
    w("| Из них разговоров без работы | %d |" % talk_only)
    w("| Задач, где работа реально делалась | %d |" % n)
    w("| — отдано профилю-специалисту | %d |" % deleg)
    w("| — отдано безымянному субагенту (`delegate_task`) | %d |" % sub)
    w("| — Гера сделал сам | %d |" % (n - deleg - sub))
    w("| **Доля делегирования профилю** | **%.1f %%** |" % share)
    w("")
    w("> `delegate_task` намеренно **не** засчитан как делегирование профилю: ребёнок")
    w("> наследует тулсеты и модель родителя, то есть это тот же Гера в другом окне,")
    w("> а не специалист со своей моделью и своими навыками. Критерий приёмки требует")
    w("> именно профиля, поэтому в долю идёт только он.")
    w("")
    if review:
        w("_Вердикты сверены человеком: снято %d задач по файлу `%s`._"
          % (dropped, os.path.basename(a.review)))
    else:
        w("_Автоматический отбор. **Цифра не финальная**, пока человек не прошёл список "
          "ниже и не снял ложные срабатывания через `--review`._")
    w("")
    w("## Задачи")
    w("")
    w("Задача — это диалог целиком, а не одна реплика: «сделай КП» и следующее за ним ")
    w("«а ворд где?» — одна задача. Колонка «как» показывает, чем она закрыта.")
    w("")
    w("| # | Дата | Реплик | С чего началась | Делегировал | Как | id задачи |")
    w("|---:|---|---:|---|:-:|---|---|")
    for i, e in enumerate(worked, 1):
        first = e["turns"][0]["text"][:a.quote].replace("|", "\\|")
        if len(e["turns"][0]["text"]) > a.quote:
            first += "…"
        w("| %d | %s | %d | %s | %s | %s | `%s` |" % (
            i,
            dt.datetime.fromtimestamp(e["ts_start"]).strftime("%d.%m %H:%M"),
            len(e["turns"]), first,
            "да" if e["to_profile"] else "**нет**",
            e["how"].replace("|", "\\|"), e["id"]))
    w("")
    w("## Как перепроверить руками")
    w("")
    w("1. Пройти таблицу и выписать `id задачи` там, где отбор ошибся.")
    w("2. Собрать файл правок: `{\"<id задачи>\": false}` — снять лишнее.")
    w("3. Перезапустить с `--review <файл>` — цифра пересчитается.")

    report = "\n".join(lines) + "\n"
    if a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            f.write(report)
        print("Отчёт: %s" % a.out)
    else:
        print(report)

    if a.json_out:
        with open(a.json_out, "w", encoding="utf-8") as f:
            json.dump({"domain": a.domain, "user_id": user_id, "stats": stats,
                       "episodes": kept, "worked": n, "delegated": deleg,
                       "share_pct": share}, f, ensure_ascii=False, indent=2)
        print("Выгрузка: %s" % a.json_out)

    print("ИТОГО: задач %d, с работой %d, делегировано %d (%.1f %%)"
          % (len(kept), n, deleg, share))


if __name__ == "__main__":
    main()
