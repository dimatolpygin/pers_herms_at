#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""profile-acceptance — приёмочный чеклист профиля. Без него профиль не выпускается.

Запускается НА СЕРВЕРЕ:

    python3 scripts/profile_acceptance.py dev            # быстрые проверки
    python3 scripts/profile_acceptance.py dev --live     # + живой прогон через kanban
    python3 scripts/profile_acceptance.py --all
    python3 scripts/profile_acceptance.py dev --json > отчёт.json

Каждый пункт — либо BLOCK (профиль не выпускается), либо WARN (записываем и живём).
Список не выдуман: каждый BLOCK стоит здесь потому, что этот отказ уже случался
на проде и стоил времени. Ссылки на первопричину — в комментариях к проверкам.

Код возврата: 0 — все BLOCK пройдены; 1 — есть проваленный BLOCK.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

try:
    import yaml
except ImportError:                                          # pragma: no cover
    sys.exit("нужен PyYAML: pip3 install pyyaml")

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

REPO = Path(__file__).resolve().parent.parent
REGISTRY = REPO / "profiles" / "registry.yaml"
HERMES_ROOT = Path(os.environ.get("HERMES_ROOT", "/root/.hermes"))
SHARED_SKILLS = HERMES_ROOT / "skills"

# Потолок промпта профиля. Смысл цифры: у Геры промпт 86 КБ и он «забывает»
# инструкции; профиль на диете держался 15 КБ и работал. 25 КБ — запас, за
# которым надо разбираться, что туда наросло, а не двигать порог.
PROMPT_LIMIT = 25_000

# Строка-ловушка. Пять профилей из шести (content, dev, seo, strategist, writer)
# до сих пор начинаются словами «Общаешься напрямую с Микой. Ты отдельный
# Telegram-бот». Профиль с ней, запущенный воркером, ведёт себя как собеседник:
# задаёт уточняющий вопрос в пустоту и ждёт ответа, которого не будет.
SOUL_TRAPS = (
    "отдельный Telegram-бот",
    "Общаешься напрямую с Микой",
)


class Report:
    def __init__(self, profile: str):
        self.profile = profile
        self.items = []

    def add(self, ok: bool, blocking: bool, name: str, detail: str = ""):
        self.items.append({
            "check": name,
            "ok": bool(ok),
            "level": "BLOCK" if blocking else "WARN",
            "detail": detail,
        })
        mark = "✓" if ok else ("✗" if blocking else "!")
        lvl = "" if ok else ("  [BLOCK]" if blocking else "  [warn]")
        print("  %s %-46s %s%s" % (mark, name, detail[:70], lvl), flush=True)

    @property
    def failed_blocking(self):
        return [i for i in self.items if not i["ok"] and i["level"] == "BLOCK"]

    @property
    def passed(self):
        return not self.failed_blocking


def sh(cmd, env=None, timeout=180, cwd=None):
    full = dict(os.environ)
    if env:
        full.update(env)
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, env=full,
                           timeout=timeout, cwd=cwd)
        return r.returncode, (r.stdout or ""), (r.stderr or "")
    except subprocess.TimeoutExpired:
        return 124, "", "timeout"


def load_registry():
    with REGISTRY.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def merged(reg, name):
    out = dict(reg.get("defaults") or {})
    out.update(reg["profiles"][name] or {})
    return out


def flow(text):
    return " ".join(str(text or "").split())


# ─────────────────────────── проверки ───────────────────────────

def run_checks(reg, name: str, live: bool) -> Report:
    p = merged(reg, name)
    home = HERMES_ROOT / "profiles" / name
    env = {"HERMES_HOME": str(home)}
    rep = Report(name)

    print("\n=== ПРИЁМКА ПРОФИЛЯ «%s» (%s) ===" % (name, p.get("title", "")))

    # 1. Профиль вообще существует.
    rep.add(home.is_dir(), True, "1. каталог профиля существует", str(home))
    if not home.is_dir():
        return rep

    # 2. Профиль установлен КАК ДИСТРИБУТИВ, а не собран руками. Без этого
    #    `hermes profile update` не сработает и профиль не переживёт падения
    #    сервера или самообновления hermes (прод уже уезжал 0.18 -> 0.20 сам).
    dist = home / "distribution.yaml"
    src = ""
    if dist.is_file():
        try:
            src = flow((yaml.safe_load(dist.read_text(encoding="utf-8")) or {}).get("source", ""))
        except Exception:
            src = ""
    rep.add(dist.is_file() and bool(src), True,
            "2. поставлен из дистрибутива (обновляем из git)", src or "нет source")

    # 3. SOUL.md без ловушки «ты отдельный Telegram-бот».
    soul_path = home / "SOUL.md"
    soul = soul_path.read_text(encoding="utf-8", errors="replace") if soul_path.is_file() else ""
    trap = next((t for t in SOUL_TRAPS if t in soul), "")
    rep.add(bool(soul) and not trap, True,
            "3. SOUL: профиль знает, что он воркер, а не бот",
            ("найдена строка «%s»" % trap) if trap else "вариант C соблюдён")

    # 4. SOUL объясняет, куда класть файл. Воркспейс задачи по умолчанию
    #    `scratch` и удаляется после завершения — результат внутри него пропадает
    #    вместе с ним, и задача выглядит сделанной при отсутствующем файле.
    art = str(p.get("artifacts", ""))
    rep.add(bool(art) and art in soul, True,
            "4. SOUL: путь для артефактов вне воркспейса", art)

    # 5. Предохранитель воркера. При 30 профиль СДЕЛАЛ документ и не успел
    #    закрыть карточку: «Iteration budget exhausted (30/30)» -> timed_out ->
    #    ретрай уже сделанной работы.
    cfg_path = home / "config.yaml"
    cfg = {}
    if cfg_path.is_file():
        try:
            cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
        except Exception as exc:
            cfg = {}
            rep.add(False, True, "5a. config.yaml читается", str(exc))
    turns = ((cfg.get("agent") or {}).get("max_turns"))
    rep.add(isinstance(turns, int) and turns >= 80, True,
            "5. agent.max_turns >= 80", "сейчас %s" % turns)

    # 6. Модель совпадает с реестром. До конвейера таблица моделей жила в трёх
    #    местах и в двух врала.
    model = (cfg.get("model") or {}).get("default")
    rep.add(model == p.get("model"), True,
            "6. модель как в реестре", "%s (реестр: %s)" % (model, p.get("model")))

    # 7. env_passthrough. Форма — СТРОКА с JSON внутри; провайдерские ключи
    #    иначе вычищаются из дочернего окружения (`_scrub_child_env`).
    passthrough = (cfg.get("terminal") or {}).get("env_passthrough")
    ok_pt = isinstance(passthrough, str) and "OPENROUTER_API_KEY" in passthrough
    rep.add(ok_pt, True, "7. terminal.env_passthrough — строка с ключом инференса",
            str(passthrough)[:60])

    # 8. .env на месте и читается.
    envf = home / ".env"
    rep.add(envf.exists(), True, "8. .env доступен",
            ("симлинк -> %s" % os.readlink(envf)) if envf.is_symlink() else "файл")

    # 9. Маркер диеты. Без него `hermes update` засеет профилю все поставочные
    #    навыки обратно, и диета проживёт до следующего самообновления.
    rep.add((home / ".no-bundled-skills").exists(), True,
            "9. маркер .no-bundled-skills (диета переживёт update)", "")

    # 10. Навыки: ровно те, что в реестре, и все симлинки живые.
    want = p.get("skills") or {}
    sk_dir = home / "skills"
    have = {}
    broken = []
    extra = []
    if sk_dir.is_dir():
        for entry in sk_dir.iterdir():
            if entry.name.startswith("."):
                continue
            if entry.is_symlink():
                have[entry.name] = os.readlink(entry)
                if not entry.resolve().is_dir():
                    broken.append(entry.name)
            elif entry.is_dir():
                extra.append(entry.name)
    missing = [k for k in want if k not in have]
    rep.add(not missing and not broken, True,
            "10. навыки профиля розданы и не битые",
            "есть %d/%d%s%s" % (len(have), len(want),
                                (", нет: " + ",".join(missing)) if missing else "",
                                (", битые: " + ",".join(broken)) if broken else ""))
    rep.add(not extra, False, "10a. в skills/ нет лишних каталогов",
            ",".join(extra[:6]) if extra else "чисто")

    # 11. Промпт не раздут — иначе профиль «забывает» собственные правила.
    #     cwd=домашний каталог профиля обязателен: `prompt-size` подмешивает
    #     AGENTS.md и файлы текущего каталога. Запуск из клона репозитория
    #     давал 31 686 Б вместо настоящих 16 005 — то есть чеклист ругался бы
    #     на профиль за содержимое каталога, из которого его проверяют.
    rc, out, _ = sh(["hermes", "prompt-size", "--json"], env=env, timeout=120,
                    cwd=str(home))
    size = None
    if rc == 0:
        try:
            data = json.loads(out)
            size = data.get("total") or data.get("system_prompt_total") or data.get("total_bytes")
        except Exception:
            size = None
    if size is None:
        m = re.search(r"(\d[\d\s,]{3,})", out)
        size = int(re.sub(r"[^\d]", "", m.group(1))) if m else None
    rep.add(bool(size) and size <= PROMPT_LIMIT, False,
            "11. промпт профиля <= %d Б" % PROMPT_LIMIT,
            "%s Б" % size if size else "не измерился")

    # 12. describe для диспетчера. Карточка с профилем без описания уедет
    #     не туда: маршрутизация идёт именно по нему, а не по имени.
    rc, out, err = sh(["hermes", "profile", "describe", name], timeout=60)
    desc = flow(out)
    same = flow(p.get("describe")) in desc or desc in flow(p.get("describe"))
    rep.add(bool(desc) and same, True,
            "12. describe записан и совпадает с реестром", desc[:60])

    # 13. Каталог артефактов существует и пишется.
    art_dir = Path(art) if art else None
    writable = False
    if art_dir:
        try:
            art_dir.mkdir(parents=True, exist_ok=True)
            probe = art_dir / ".acceptance_probe"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
            writable = True
        except Exception:
            writable = False
    rep.add(writable, True, "13. каталог артефактов существует и пишется", art)

    # 14. Транспорт со стороны Геры. `kanban_create` появляется у Геры, только
    #     если в глобальном ключе toolsets его config.yaml есть kanban.
    gera_cfg = HERMES_ROOT / "config.yaml"
    toolsets = ""
    if gera_cfg.is_file():
        try:
            toolsets = str((yaml.safe_load(gera_cfg.read_text(encoding="utf-8")) or {}).get("toolsets", ""))
        except Exception:
            toolsets = ""
    rep.add("kanban" in toolsets, True,
            "14. у Геры включён toolset kanban (транспорт)", toolsets[:50])

    # 15. Живой прогон: карточка -> диспетчер -> воркер -> done. Единственная
    #     проверка, которая доказывает, что профиль реально работает.
    if live:
        rep_live(rep, reg, name, p)

    return rep


def rep_live(rep: Report, reg, name: str, p: dict) -> None:
    """Заводит настоящую карточку на профиль и ждёт её закрытия."""
    probe = (p.get("acceptance_probe") or {})
    title = probe.get("title") or ("Приёмка профиля %s" % name)
    body = probe.get("body") or (
        "Это приёмочная проверка конвейера, не боевая задача. "
        "Сделай минимальный результат по своей специальности, положи файл в %s "
        "и верни абсолютный путь к нему." % p.get("artifacts")
    )
    runtime = probe.get("max_runtime", "20m")

    print("  … живой прогон: завожу карточку на %s" % name, flush=True)
    rc, out, err = sh([
        "hermes", "kanban", "create", title,
        "--body", body, "--assignee", name,
        "--max-runtime", str(runtime), "--json",
    ], timeout=120)
    task_id = ""
    try:
        task_id = (json.loads(out) or {}).get("id", "")
    except Exception:
        m = re.search(r"\b(t_[0-9a-f]+)\b", out + err)
        task_id = m.group(1) if m else ""
    if not task_id:
        rep.add(False, True, "15. живой прогон: карточка заведена", (out + err)[:70])
        return
    rep.add(True, True, "15. живой прогон: карточка заведена", task_id)

    # Диспетчер в gateway забирает карточку за <= 60 с. Ждём до max_runtime + запас.
    deadline = time.time() + _seconds(runtime) + 300
    status = "?"
    claimed_at = None
    while time.time() < deadline:
        time.sleep(20)
        rc, out, _ = sh(["hermes", "kanban", "list", "--json"], timeout=60)
        try:
            tasks = json.loads(out)
            tasks = tasks.get("tasks", tasks) if isinstance(tasks, dict) else tasks
            row = next((t for t in tasks if t.get("id") == task_id), None)
        except Exception:
            row = None
        if not row:
            continue
        status = row.get("status", "?")
        if status == "running" and claimed_at is None:
            claimed_at = time.time()
        if status in ("done", "review", "blocked"):
            break

    rep.add(claimed_at is not None, True,
            "16. диспетчер подхватил карточку (профиль поднялся)", "статус: %s" % status)
    rep.add(status in ("done", "review"), True,
            "17. задача закрыта профилем", "статус: %s" % status)

    # Артефакт: файл должен лежать ВНЕ воркспейса, иначе он уже удалён.
    art_dir = Path(p.get("artifacts", "/nonexistent"))
    fresh = []
    if art_dir.is_dir():
        cutoff = time.time() - 3600
        fresh = [f.name for f in art_dir.iterdir()
                 if f.is_file() and f.stat().st_mtime > cutoff]
    rep.add(bool(fresh), True, "18. результат лежит в каталоге артефактов",
            ", ".join(fresh[:3]) if fresh else "пусто — файл, вероятно, умер вместе с scratch")


def _seconds(spec) -> int:
    s = str(spec).strip()
    m = re.match(r"^(\d+)\s*([smhd]?)$", s)
    if not m:
        return 1200
    n, unit = int(m.group(1)), m.group(2)
    return n * {"": 1, "s": 1, "m": 60, "h": 3600, "d": 86400}[unit]


# ─────────────────────────── CLI ───────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("name", nargs="?")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--live", action="store_true",
                    help="завести настоящую карточку и дождаться её закрытия")
    ap.add_argument("--json", dest="as_json", metavar="FILE", nargs="?", const="-",
                    help="выгрузить отчёт машинно")
    args = ap.parse_args()

    reg = load_registry()
    names = sorted(reg["profiles"]) if args.all else ([args.name] if args.name else [])
    if not names:
        sys.exit("укажи профиль или --all")

    reports = [run_checks(reg, n, args.live) for n in names]

    print()
    failed = 0
    for r in reports:
        bad = r.failed_blocking
        failed += len(bad)
        verdict = "ПРОШЁЛ" if r.passed else "НЕ ПРОШЁЛ (%d блокеров)" % len(bad)
        warns = sum(1 for i in r.items if not i["ok"] and i["level"] == "WARN")
        print("%-12s %s%s" % (r.profile, verdict, (", замечаний: %d" % warns) if warns else ""))

    if args.as_json:
        payload = {
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "live": args.live,
            "profiles": [{"profile": r.profile, "passed": r.passed, "checks": r.items}
                         for r in reports],
        }
        text = json.dumps(payload, ensure_ascii=False, indent=2)
        if args.as_json == "-":
            print(text)
        else:
            Path(args.as_json).write_text(text, encoding="utf-8", newline="\n")
            print("отчёт: %s" % args.as_json)

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
