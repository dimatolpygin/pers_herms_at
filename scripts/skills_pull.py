#!/usr/bin/env python3
"""
skills_pull — забрать навыки с прод-сервера в репозиторий.

Зачем: агент создаёт и правит навыки на сервере (`skill_manage` пишет в
`~/.hermes/skills`), а источник правды проекта — репозиторий (`hermes-skills/`).
Без синхронизации созданный агентом навык живёт только на сервере и пропадёт
при его пересоздании. Так уже случилось: навык `lead-magnets` был создан на проде
09.07.2026 и в git не попал.

Почему скрипт запускается локально, а не по крону на сервере:
у сервера НЕТ права записи в GitHub (только анонимный https), а класть туда ключ
с правом записи не стоит — на проде у самого агента есть root-терминал, то есть
ключ оказался бы в пределах его досягаемости. Здесь push идёт правами человека.

Что делает:
  1. по SSH получает список СВОИХ навыков (не входящих в поставку hermes);
  2. копирует их в hermes-skills/<категория>/<навык>;
  3. показывает, что изменилось;
  4. по флагу --commit коммитит в отдельную ветку (не в master).

Использование:
    python scripts/skills_pull.py                 # показать расхождения, ничего не менять
    python scripts/skills_pull.py --apply --only lead-magnets   # забрать конкретные навыки
    python scripts/skills_pull.py --apply --commit  # + коммит в ветку skills-from-prod

ВНИМАНИЕ: синк односторонний, прод → репо. Если навык правился локально и ещё не
уехал на сервер, слепой --apply затрёт локальную правку прод-версией. Сначала
смотри вывод без флагов и бери нужное через --only.

Доступ к серверу берётся из переменных окружения HERMES_SSH_HOST / _USER /
_PASSWORD, иначе парсится `сервер.txt` в корне репо (файл вне git).
Требует `paramiko` (pip install paramiko).
"""
from __future__ import annotations

import argparse
import io
import os
import re
import subprocess
import sys
from pathlib import Path

# Русская Windows: без этого print падает на '→'/эмодзи (грабли этапа 6).
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILLS_DST = REPO_ROOT / "hermes-skills"
BRANCH = "skills-from-prod"

# Навыки, которые НЕ тянем в репозиторий даже если они «свои» по формальному
# признаку. Пополняется осознанно.
EXCLUDE: set[str] = set()

# Скрипт исполняется на сервере: печатает по строке "имя<TAB>путь" для каждого
# навыка, которого нет в поставке hermes. Разделение builtin/своё берём из
# .bundled_manifest (эталон поставки) — метка curator'а «agent-created» для этого
# не годится: она стоит и на штатных навыках (проверено — 72 из 82).
REMOTE_PROBE = r'''
import os, sys
HOME = os.path.expanduser("~/.hermes")
SKILLS = os.path.join(HOME, "skills")
bundled = set()
mpath = os.path.join(SKILLS, ".bundled_manifest")
if os.path.exists(mpath):
    for line in open(mpath, encoding="utf-8", errors="replace"):
        line = line.strip()
        if line and ":" in line:
            bundled.add(line.rsplit(":", 1)[0])

def frontmatter_name(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            if fh.readline().strip() != "---":
                return None
            for line in fh:
                if line.strip() == "---":
                    break
                if line.startswith("name:"):
                    return line.split(":", 1)[1].strip().strip("\"'")
    except Exception:
        pass
    return None

for root, dirs, files in os.walk(SKILLS):
    dirs[:] = [d for d in dirs if not d.startswith(".")]
    if "SKILL.md" not in files:
        continue
    skill_md = os.path.join(root, "SKILL.md")
    name = frontmatter_name(skill_md) or os.path.basename(root)
    # Имя из фронтматтера может отличаться от папки (postmypost-publish ->
    # social-publishing/postmypost), поэтому сверяем оба.
    if name in bundled or os.path.basename(root) in bundled:
        continue
    sys.stdout.write(name + "\t" + root + "\n")
'''


def read_server_credentials() -> tuple[str, str, str]:
    """Хост/пользователь/пароль из окружения либо из сервер.txt (вне git)."""
    host = os.environ.get("HERMES_SSH_HOST")
    user = os.environ.get("HERMES_SSH_USER", "root")
    password = os.environ.get("HERMES_SSH_PASSWORD")
    if host and password:
        return host, user, password

    notes = REPO_ROOT / "сервер.txt"
    if not notes.exists():
        sys.exit(
            "Не найдены доступы: задай HERMES_SSH_HOST/HERMES_SSH_USER/"
            "HERMES_SSH_PASSWORD или положи сервер.txt в корень репозитория."
        )
    text = notes.read_text(encoding="utf-8", errors="replace")
    match = re.search(
        r"ssh\s+(?P<user>[\w.-]+)@(?P<host>[\d.]+).*?пароль:\s*(?P<password>\S+)",
        text,
        re.S,
    )
    if not match:
        sys.exit("Не удалось вычитать ssh-доступы из сервер.txt.")
    return match["host"], match["user"], match["password"]


def connect():
    try:
        import paramiko
    except ImportError:
        sys.exit("Нужен paramiko: pip install paramiko")

    host, user, password = read_server_credentials()
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(host, username=user, password=password, timeout=30)
    return client


def list_own_skills(client) -> list[tuple[str, str]]:
    """[(имя, путь на сервере)] для навыков, не входящих в поставку hermes."""
    stdin, stdout, stderr = client.exec_command("python3 -", timeout=120)
    stdin.write(REMOTE_PROBE)
    stdin.channel.shutdown_write()
    out = stdout.read().decode("utf-8", "replace")
    err = stderr.read().decode("utf-8", "replace")
    if stdout.channel.recv_exit_status() != 0:
        sys.exit(f"Не удалось получить список навыков с сервера:\n{err}")

    skills = []
    for line in out.splitlines():
        if "\t" not in line:
            continue
        name, path = line.split("\t", 1)
        if name in EXCLUDE:
            continue
        skills.append((name, path))
    return sorted(skills)


def pull_skill(sftp, remote_dir: str, local_dir: Path, apply: bool) -> list[str]:
    """Копирует дерево навыка. Возвращает список изменившихся файлов."""
    changed: list[str] = []
    stack = [(remote_dir, local_dir)]
    while stack:
        rdir, ldir = stack.pop()
        for entry in sftp.listdir_attr(rdir):
            if entry.filename.startswith(".") or entry.filename == "__pycache__":
                continue
            rpath = f"{rdir}/{entry.filename}"
            lpath = ldir / entry.filename
            if entry.st_mode is not None and (entry.st_mode & 0o40000):
                stack.append((rpath, lpath))
                continue

            buf = io.BytesIO()
            sftp.getfo(rpath, buf)
            new = buf.getvalue()
            old = lpath.read_bytes() if lpath.exists() else None
            if old == new:
                continue
            changed.append(str(lpath.relative_to(REPO_ROOT)).replace("\\", "/"))
            if apply:
                lpath.parent.mkdir(parents=True, exist_ok=True)
                lpath.write_bytes(new)
    return changed


def git(*args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, encoding="utf-8"
    )
    if result.returncode != 0:
        sys.exit(f"git {' '.join(args)} → {result.stderr.strip()}")
    return result.stdout.strip()


def main() -> int:
    parser = argparse.ArgumentParser(description="Забрать навыки с прод-сервера в репозиторий.")
    parser.add_argument("--apply", action="store_true", help="записать файлы (иначе только показать)")
    parser.add_argument("--commit", action="store_true", help="закоммитить в ветку %s" % BRANCH)
    parser.add_argument("--push", action="store_true", help="и запушить ветку")
    parser.add_argument(
        "--only",
        nargs="+",
        metavar="НАВЫК",
        help="работать только с этими навыками (по имени). Без него берутся все.",
    )
    parser.add_argument(
        "--skip", nargs="+", metavar="НАВЫК", default=[], help="пропустить эти навыки"
    )
    args = parser.parse_args()

    client = connect()
    try:
        skills = list_own_skills(client)
        print(f"Своих навыков на проде: {len(skills)}")

        if args.only:
            unknown = set(args.only) - {n for n, _ in skills}
            if unknown:
                sys.exit(f"Нет таких навыков на проде: {', '.join(sorted(unknown))}")
            skills = [s for s in skills if s[0] in set(args.only)]
        if args.skip:
            skills = [s for s in skills if s[0] not in set(args.skip)]
        if args.only or args.skip:
            print(f"Отобрано: {', '.join(n for n, _ in skills) or '—'}")
        print()

        sftp = client.open_sftp()
        all_changed: list[str] = []
        for name, remote_dir in skills:
            category = os.path.basename(os.path.dirname(remote_dir))
            folder = os.path.basename(remote_dir)
            local_dir = SKILLS_DST / category / folder
            status = "новый" if not local_dir.exists() else "есть в репо"
            changed = pull_skill(sftp, remote_dir, local_dir, args.apply)
            all_changed.extend(changed)
            mark = f"{len(changed)} файл(ов) расходятся" if changed else "совпадает"
            print(f"  {name:22} [{status:11}] {mark}")
        sftp.close()
    finally:
        client.close()

    if not all_changed:
        print("\nРасхождений нет — репозиторий совпадает с продом.")
        return 0

    print(f"\nРасходятся {len(all_changed)} файл(ов):")
    for path in all_changed:
        print("  " + path)

    if not args.apply:
        print("\nЭто был просмотр. Записать: --apply (плюс --commit для коммита в ветку).")
        return 0

    if not args.commit:
        print("\nФайлы записаны в рабочее дерево. Проверь `git diff` и коммить сам.")
        return 0

    # Коммитим в отдельную ветку: master остаётся за человеком.
    current = git("rev-parse", "--abbrev-ref", "HEAD")
    git("checkout", "-B", BRANCH)
    git("add", *all_changed)
    git("commit", "-m", "chore(skills): забрать навыки с прод-сервера\n\n"
        "Автоматически собрано scripts/skills_pull.py.")
    print(f"\nКоммит в ветке {BRANCH}: {git('rev-parse', '--short', 'HEAD')}")
    if args.push:
        git("push", "-u", "origin", BRANCH)
        print(f"Ветка {BRANCH} запушена — открой PR и посмотри, что приехало.")
    else:
        print(f"Пуш: git push -u origin {BRANCH}")
    print(f"Вернуться: git checkout {current}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
