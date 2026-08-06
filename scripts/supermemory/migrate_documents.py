"""Разовая заливка накопленной памяти Хермеса в supermemory (этап 17).

Идемпотентна по customId: повторный запуск не плодит дубли.
Пароли (mika-passwords.md) НЕ заливаются намеренно.
"""
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

API = "https://api.supermemory.ai/v3/documents"
CONTAINER = "hermes_mika"
EXCLUDE = {"mika-passwords.md"}

key = os.environ.get("SUPERMEMORY_API_KEY", "")
if not key:
    for line in Path("/root/.hermes/.env").read_text(encoding="utf-8").splitlines():
        if line.startswith("SUPERMEMORY_API_KEY="):
            key = line.split("=", 1)[1].strip()
if not key:
    sys.exit("нет SUPERMEMORY_API_KEY")

targets = []
for p in sorted(Path("/root/.hermes/memories").glob("*.md")):
    targets.append(("memory", p))
for p in sorted(Path("/root/.hermes/notes").glob("*.md")):
    targets.append(("note", p))

ok = skipped = failed = 0
for kind, path in targets:
    if path.name in EXCLUDE:
        print("ПРОПУСК (секреты): %s" % path.name)
        skipped += 1
        continue
    text = path.read_text(encoding="utf-8", errors="replace").strip()
    if not text:
        print("ПРОПУСК (пусто): %s" % path.name)
        skipped += 1
        continue

    body = {
        "content": "# %s\n\n%s" % (path.stem, text),
        "containerTag": CONTAINER,
        "customId": "hermes-migration-%s-%s" % (kind, path.stem),
        "metadata": {"sm_source": "hermes", "origin": kind, "file": path.name},
    }
    req = urllib.request.Request(
        API,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": "Bearer %s" % key,
            "Content-Type": "application/json",
            "x-sm-source": "hermes",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            resp = json.loads(r.read().decode("utf-8"))
        print("OK  %-28s %5d симв.  id=%s" % (path.name, len(text), resp.get("id", "?")))
        ok += 1
    except urllib.error.HTTPError as e:
        print("FAIL %-27s HTTP %s %s" % (path.name, e.code, e.read().decode("utf-8", "replace")[:200]))
        failed += 1
    except Exception as e:
        print("FAIL %-27s %r" % (path.name, e))
        failed += 1

print("\nИТОГ: залито %d, пропущено %d, ошибок %d" % (ok, skipped, failed))
sys.exit(1 if failed else 0)
