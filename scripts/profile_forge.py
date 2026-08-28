#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""profile-forge — сборка профиля hermes из реестра и шаблона, одной командой.

Инвариант 1 блока v8: профиль — продукт повторяемого конвейера, а не ручная
поделка. Проверочный вопрос к любому шагу: «а восьмой профиль встанет по этому
же пути за час?». Этот скрипт и есть путь.

Два режима, намеренно разделённые:

  render   — читает profiles/registry.yaml + profiles/_template/*.tmpl и пишет
             profiles/<имя>/{SOUL.md,config.yaml,distribution.yaml,skills.txt}.
             Работает где угодно (в т.ч. на машине разработчика), в git едет
             результат рендера — чтобы дифф был виден человеку, а не прятался
             внутри генератора.

  install  — ставит отрендеренный профиль на сервер ШТАТНЫМ
             `hermes profile install`, затем доделывает то, чего в нём нет:
             .env, симлинки навыков, маркер .no-bundled-skills, каталог
             артефактов, `hermes profile describe`, пересборку ROUTING.md.

Почему установка именно так, а не `hermes profile create`:
  * `create` засевает в профиль ПОЛНЫЙ поставочный комплект навыков (67 штук) —
    потом их приходится вычищать диетой. `install` из дистрибутива не засевает
    ничего: профиль встаёт пустым.
  * `install` записывает источник в distribution.yaml, и профиль после этого
    обновляется одной командой `hermes profile update <имя>` — при этом память,
    сессии, auth.json и .env не трогаются вовсе. Это и есть «профиль живёт в git».

Навыки в дистрибутив НЕ кладутся: он не может содержать симлинков
(`_reject_distribution_symlinks`), а навыки профиля — симлинки в общий
/root/.hermes/skills/. Раздаёт их этот скрипт.

Примеры:
    python3 scripts/profile_forge.py --check                  # проверить реестр
    python3 scripts/profile_forge.py render --all             # перерендерить всё
    python3 scripts/profile_forge.py render dev
    python3 scripts/profile_forge.py install dev              # на сервере
    python3 scripts/profile_forge.py install dev --dry-run
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

try:
    import yaml
except ImportError:                                          # pragma: no cover
    sys.exit("нужен PyYAML: pip3 install pyyaml")

# Скрипт печатает по-русски, а вызывают его и с Windows (git-bash, cp1251),
# и с сервера. Без этого вывод падает на первом же символе не из кодировки ОС.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

# ─────────────────────────── пути ───────────────────────────

REPO = Path(__file__).resolve().parent.parent
REGISTRY = REPO / "profiles" / "registry.yaml"
TEMPLATE_DIR = REPO / "profiles" / "_template"
EXTRA_DIR = REPO / "profiles" / "_extra"

# Дом hermes на сервере. Переопределяется переменной окружения — так же, как
# адресуется сам hermes, чтобы не промахнуться мимо Геры.
HERMES_ROOT = Path(os.environ.get("HERMES_ROOT", "/root/.hermes"))
SHARED_SKILLS = HERMES_ROOT / "skills"
ROUTING_MD = SHARED_SKILLS / "meta" / "profile-delegate" / "ROUTING.md"

DEFAULT_VERSION = "1.0.0"


# ─────────────────────────── утилиты ───────────────────────────

def load_registry() -> dict:
    with REGISTRY.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict) or "profiles" not in data:
        sys.exit(f"{REGISTRY}: нет ключа profiles")
    return data


def merged(reg: dict, name: str) -> dict:
    """Блок профиля поверх defaults. Списки и словари не сливаются — берётся
    тот, что задан в профиле целиком: полусклеенный env_passthrough опаснее
    отсутствующего."""
    if name not in reg["profiles"]:
        known = ", ".join(sorted(reg["profiles"]))
        sys.exit(f"профиля '{name}' нет в реестре. Есть: {known}")
    out = dict(reg.get("defaults") or {})
    out.update(reg["profiles"][name] or {})
    out["name"] = name
    return out


def flow(text) -> str:
    """YAML-скаляр `>-` приезжает с переводами строк. Для describe и critic_why
    нужен один абзац."""
    if text is None:
        return ""
    return " ".join(str(text).split())


def render(tmpl: str, mapping: dict) -> str:
    out = tmpl
    for key, value in mapping.items():
        out = out.replace("{{%s}}" % key, str(value))
    left = [c for c in ("{{",) if c in out]
    if left:
        # Незаменённый плейсхолдер — это молча кривой профиль, а не мелочь.
        bad = out[out.index("{{"):out.index("{{") + 40]
        sys.exit(f"в шаблоне остался незаполненный плейсхолдер: {bad!r}")
    return out


def run(cmd: list, env: dict | None = None, check: bool = True) -> str:
    full = dict(os.environ)
    if env:
        full.update(env)
    res = subprocess.run(cmd, capture_output=True, text=True, env=full)
    if check and res.returncode != 0:
        sys.exit(
            "команда упала: %s\n--- stdout ---\n%s\n--- stderr ---\n%s"
            % (" ".join(cmd), res.stdout, res.stderr)
        )
    return (res.stdout or "") + (res.stderr or "")


# ─────────────────────────── render ───────────────────────────

def render_profile(reg: dict, name: str) -> Path:
    p = merged(reg, name)
    out_dir = REPO / "profiles" / name
    out_dir.mkdir(parents=True, exist_ok=True)

    # --- SOUL.md ---
    duties = "\n".join("- %s" % flow(d) for d in p.get("duties") or [])
    rules = p.get("rules") or []
    rules_block = ""
    if rules:
        rules_block = "\n## Профильные правила\n\n" + "\n".join(
            "- %s" % flow(r) for r in rules
        ) + "\n"

    # Профильная врезка: то, что шаблонизировать нельзя и что принадлежит
    # конкретному профилю — реквизиты ИП, правила оформления, рабочий сниппет
    # вызова критика. Лежит отдельным файлом profiles/_extra/<имя>.md, чтобы
    # реестр оставался реестром, а не свалкой. Файла нет — врезки нет.
    extra_path = EXTRA_DIR / ("%s.md" % name)
    extra = ""
    if extra_path.is_file():
        extra = "\n" + extra_path.read_text(encoding="utf-8").rstrip() + "\n"

    soul = render((TEMPLATE_DIR / "SOUL.md.tmpl").read_text(encoding="utf-8"), {
        "NAME": name,
        "TITLE": p.get("title", name),
        "MODEL": p["model"],
        "CRITIC": p["critic"],
        "CRITIC_WHY": flow(p.get("critic_why")),
        "DESCRIBE": flow(p["describe"]),
        "DUTIES": duties,
        "ARTIFACTS": p["artifacts"],
        "RULES": rules_block,
        "EXTRA": extra,
    })
    (out_dir / "SOUL.md").write_text(soul, encoding="utf-8", newline="\n")

    # --- config.yaml ---
    mem = p.get("memory") or {}
    comp = p.get("compression") or {}
    # env_passthrough hermes читает как СТРОКУ с JSON внутри — форму сохраняем.
    passthrough = json.dumps(p.get("env_passthrough") or [], ensure_ascii=False)
    cfg = render((TEMPLATE_DIR / "config.yaml.tmpl").read_text(encoding="utf-8"), {
        "MODEL": p["model"],
        "PROVIDER": p.get("provider", "openrouter"),
        "MAX_TURNS": p.get("max_turns", 80),
        "REASONING_EFFORT": p.get("reasoning_effort", "low"),
        "ENV_PASSTHROUGH": passthrough,
        "MEMORY_ENABLED": str(mem.get("memory_enabled", True)).lower(),
        "USER_PROFILE_ENABLED": str(mem.get("user_profile_enabled", False)).lower(),
        "MEMORY_CHAR_LIMIT": mem.get("memory_char_limit", 15000),
        "MEMORY_PROVIDER": mem.get("provider", "supermemory"),
        "SUBAGENT_MODEL": p.get("subagent_model", "google/gemini-3.7-flash"),
        "COMPRESSION_ENABLED": str(comp.get("enabled", True)).lower(),
        "COMPRESSION_THRESHOLD": comp.get("threshold", 0.4),
        "COMPRESSION_TARGET_RATIO": comp.get("target_ratio", 0.15),
        "TIMEZONE": p.get("timezone", "Europe/Moscow"),
    })
    (out_dir / "config.yaml").write_text(cfg, encoding="utf-8", newline="\n")

    # --- distribution.yaml ---
    # env_requires выводится из env_passthrough, а не пишется отдельным списком:
    # два ручных списка одного и того же расходятся — это уже случалось с
    # таблицей моделей. Из них hermes генерирует .env.EXAMPLE при установке.
    descriptions = p.get("env_descriptions") or {}
    required = set(p.get("env_required") or [])
    lines = []
    for var in p.get("env_passthrough") or []:
        lines.append("  - name: %s" % var)
        lines.append('    description: "%s"' % descriptions.get(var, ""))
        lines.append("    required: %s" % str(var in required).lower())
    dist = render((TEMPLATE_DIR / "distribution.yaml.tmpl").read_text(encoding="utf-8"), {
        "NAME": name,
        "TITLE": p.get("title", name),
        "VERSION": p.get("version", DEFAULT_VERSION),
        "AUTHOR": p.get("author", ""),
        "HERMES_REQUIRES": p.get("hermes_requires", ">=0.20.0"),
        "ENV_REQUIRES": "\n".join(lines) if lines else "  []",
    })
    (out_dir / "distribution.yaml").write_text(dist, encoding="utf-8", newline="\n")

    # --- skills.txt (человекочитаемая опись; ставится симлинками) ---
    skills = p.get("skills") or {}
    head = [
        "# Навыки профиля %s — СГЕНЕРИРОВАНО scripts/profile_forge.py." % name,
        "# Все они — симлинки в общие навыки Геры (%s)." % SHARED_SKILLS,
        "# Файлы не копируются: удалить навык у профиля можно, удалить сам навык —",
        "# нельзя, он сломается у Геры и во всех профилях сразу.",
        "# Раздача: python3 scripts/profile_forge.py install %s" % name,
        "",
    ]
    width = max((len(k) for k in skills), default=1)
    body = ["%-*s -> %s" % (width, k, v) for k, v in skills.items()]
    (out_dir / "skills.txt").write_text(
        "\n".join(head + body) + "\n", encoding="utf-8", newline="\n"
    )

    return out_dir


# ─────────────────────────── check ───────────────────────────

def check(reg: dict) -> int:
    """Проверки, которые дешевле сделать до установки, чем ловить на проде."""
    problems = []
    seen_desc = {}
    for name in sorted(reg["profiles"]):
        p = merged(reg, name)
        for field in ("model", "critic", "describe", "artifacts"):
            if not p.get(field):
                problems.append("%s: нет обязательного поля %s" % (name, field))
        # Критик из той же семьи — не критик (раздел 6 ТЗ v8). Исключение
        # заявлено в реестре явным равенством модели и критика (content).
        fam = lambda m: str(m).split("/")[0]
        if p.get("model") and p.get("critic"):
            if fam(p["model"]) == fam(p["critic"]) and p["model"] != p["critic"]:
                problems.append(
                    "%s: критик %s из той же семьи, что модель %s — cross-check "
                    "не настоящий" % (name, p["critic"], p["model"])
                )
        # «Не сюда» обязательно: с восемью профилями зоны пересекаются.
        d = flow(p.get("describe", ""))
        if "е сюда" not in d and "е идёт" not in d:
            problems.append(
                "%s: в describe нет фразы «Не сюда: …» — маршрутизация "
                "восьмого профиля начнёт промахиваться" % name
            )
        seen_desc[name] = d
        # Навыки: имя симлинка не должно совпадать у двух разных путей.
        for link, target in (p.get("skills") or {}).items():
            if "/" in link:
                problems.append("%s: имя симлинка '%s' не должно содержать /" % (name, link))
            if SHARED_SKILLS.exists() and not (SHARED_SKILLS / target).is_dir():
                problems.append("%s: навыка %s нет в %s" % (name, target, SHARED_SKILLS))

    if not TEMPLATE_DIR.is_dir():
        problems.append("нет каталога шаблона %s" % TEMPLATE_DIR)

    for name, desc in seen_desc.items():
        print("  %-12s %s" % (name, desc[:78]))
    print()
    if problems:
        print("НАЙДЕНЫ ПРОБЛЕМЫ (%d):" % len(problems))
        for x in problems:
            print("  ✗ %s" % x)
        return 1
    print("реестр в порядке: %d профилей" % len(reg["profiles"]))
    return 0


# ─────────────────────────── install ───────────────────────────

def install_profile(reg: dict, name: str, dry: bool = False) -> float:
    """Ставит профиль на сервер. Возвращает время сборки в секундах —
    оно нужно как критерий приёмки этапа («время сборки записано»)."""
    p = merged(reg, name)
    src = REPO / "profiles" / name
    if not (src / "distribution.yaml").is_file():
        sys.exit("сначала отрендерь: python3 scripts/profile_forge.py render %s" % name)

    home = HERMES_ROOT / "profiles" / name
    env = {"HERMES_HOME": str(home)}
    steps = []
    t0 = time.time()

    def say(msg):
        steps.append(msg)
        print("  " + msg, flush=True)

    # 1. Штатная установка дистрибутива.
    cmd = ["hermes", "profile", "install", str(src), "--name", name, "--force", "-y"]
    if dry:
        say("[dry] " + " ".join(cmd))
    else:
        run(cmd)
        say("установлен из дистрибутива %s" % src)

    # 2. .env. Профиль наследует общий .env Геры симлинком — так уже сделано
    #    клиентом для всех шести профилей. Это осознанный компромисс: в .env
    #    лежат ключи, которые профилю не нужны (управляющий ключ OpenRouter,
    #    почтовые пароли). Сузить нельзя, пока на тех же ключах живут навыки
    #    balance и s3-upload — это отдельная позиция «приватность профиля».
    env_link = home / ".env"
    if dry:
        say("[dry] .env -> %s/.env" % HERMES_ROOT)
    else:
        if env_link.is_symlink() or env_link.exists():
            env_link.unlink()
        env_link.symlink_to(HERMES_ROOT / ".env")
        say(".env -> %s/.env" % HERMES_ROOT)

    # 3. Маркер: не засевать поставочные навыки при `hermes update`.
    #    Без него самообновление hermes (прод уже уезжал 0.18 -> 0.20 сам)
    #    вернёт профилю все 67 навыков и съест диету.
    marker = home / ".no-bundled-skills"
    if dry:
        say("[dry] маркер .no-bundled-skills")
    else:
        marker.touch()
        say("маркер .no-bundled-skills поставлен")

    # 4. Навыки — симлинками в общий каталог.
    skills_dir = home / "skills"
    want = p.get("skills") or {}
    if not dry:
        skills_dir.mkdir(parents=True, exist_ok=True)
        # Лишние симлинки убираем; настоящие каталоги не трогаем вовсе —
        # в чужом каталоге может лежать не наше.
        for entry in skills_dir.iterdir():
            if entry.is_symlink() and entry.name not in want:
                entry.unlink()
        for link, target in want.items():
            dst = skills_dir / link
            tgt = SHARED_SKILLS / target
            if not tgt.is_dir():
                sys.exit("навыка нет: %s" % tgt)
            if dst.is_symlink() or dst.exists():
                if dst.is_symlink():
                    dst.unlink()
                else:
                    sys.exit("в %s лежит настоящий каталог, а не симлинк — разберись руками" % dst)
            dst.symlink_to(tgt)
    say("навыков роздано: %d" % len(want))

    # 5. Каталог артефактов вне воркспейса задачи.
    art = Path(p["artifacts"])
    if dry:
        say("[dry] каталог артефактов %s" % art)
    else:
        art.mkdir(parents=True, exist_ok=True)
        say("каталог артефактов %s" % art)

    # 6. Описание для диспетчера kanban — из реестра, не из прозы в SOUL.
    dcmd = ["hermes", "profile", "describe", name, "--overwrite", "--text", flow(p["describe"])]
    if dry:
        say("[dry] describe")
    else:
        run(dcmd)
        say("describe записан (%d симв.)" % len(flow(p["describe"])))

    elapsed = time.time() - t0
    print("  готово за %.1f с" % elapsed)
    return elapsed


def build_routing(reg: dict, dry: bool = False) -> None:
    """ROUTING.md для навыка Геры meta/profile-delegate.

    Модель берётся из `hermes profile list`, а не пишется руками: в навыке
    клиента таблица моделей уже разошлась с реальностью (seo и dev значились
    на Gemini Flash, фактически стояли DeepSeek и Qwen)."""
    listing = run(["hermes", "profile", "list"], check=False) if not dry else ""
    models = {}
    for line in listing.splitlines():
        parts = line.replace("◆", " ").split()
        if len(parts) >= 2 and parts[0] in reg["profiles"]:
            models[parts[0]] = parts[1]

    lines = [
        "<!-- СГЕНЕРИРОВАНО scripts/profile_forge.py — руками не править. -->",
        "<!-- Обновить: python3 scripts/profile_forge.py routing -->",
        "",
        "# Кому какая задача",
        "",
        "Источник — `profiles/registry.yaml`. Модель — из `hermes profile list`.",
        "Дата сборки: %s." % time.strftime("%Y-%m-%d %H:%M %Z"),
        "",
    ]
    for name in sorted(reg["profiles"]):
        p = merged(reg, name)
        lines += [
            "## `%s` — %s" % (name, p.get("title", name)),
            "",
            "Модель: %s · критик: %s" % (models.get(name, p["model"]), p["critic"]),
            "",
            flow(p["describe"]),
            "",
        ]
    lines += [
        "---",
        "",
        "Имя профиля в `kanban_create(assignee=...)` пишется ровно так, как в заголовке.",
        "Карточка с неизвестным assignee молча остаётся в `ready` навсегда.",
        "",
    ]
    text = "\n".join(lines)
    if dry:
        print(text)
        return
    ROUTING_MD.parent.mkdir(parents=True, exist_ok=True)
    ROUTING_MD.write_text(text, encoding="utf-8", newline="\n")
    print("ROUTING.md собран: %s (%d Б)" % (ROUTING_MD, len(text.encode("utf-8"))))


# ─────────────────────────── CLI ───────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true", help="проверить реестр и выйти")
    sub = ap.add_subparsers(dest="cmd")

    r = sub.add_parser("render", help="сгенерировать файлы профиля в profiles/<имя>/")
    r.add_argument("name", nargs="?")
    r.add_argument("--all", action="store_true")

    i = sub.add_parser("install", help="поставить профиль на сервер")
    i.add_argument("name")
    i.add_argument("--dry-run", action="store_true")

    sub.add_parser("routing", help="пересобрать ROUTING.md для навыка Геры")

    args = ap.parse_args()
    reg = load_registry()

    if args.check or args.cmd is None:
        return check(reg)

    if args.cmd == "render":
        names = sorted(reg["profiles"]) if args.all else [args.name]
        if not names or names == [None]:
            sys.exit("укажи имя профиля или --all")
        for n in names:
            out = render_profile(reg, n)
            print("отрендерен %s -> %s" % (n, out))
        return 0

    if args.cmd == "install":
        install_profile(reg, args.name, dry=args.dry_run)
        build_routing(reg, dry=args.dry_run)
        return 0

    if args.cmd == "routing":
        build_routing(reg)
        return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
