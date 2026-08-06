"""Заливка накопленной памяти как диалога — этот путь запускает извлечение фактов
и построение профиля (документный ингест этого не делает). Этап 17.
Пароли (mika-passwords.md) НЕ заливаются намеренно.
"""
import json
import urllib.error
import urllib.request
from pathlib import Path

EXCLUDE = {"mika-passwords.md"}
TAG = "hermes_mika"

key = [l.split("=", 1)[1].strip() for l in Path("/root/.hermes/.env").read_text(encoding="utf-8").splitlines()
       if l.startswith("SUPERMEMORY_API_KEY=")][0]

files = [("память агента", p) for p in sorted(Path("/root/.hermes/memories").glob("*.md"))]
files += [("заметка", p) for p in sorted(Path("/root/.hermes/notes").glob("*.md"))]

messages = []
used = 0
for kind, path in files:
    if path.name in EXCLUDE:
        continue
    text = path.read_text(encoding="utf-8", errors="replace").strip()
    if not text:
        continue
    messages.append({"role": "user",
                     "content": "Запомни, это %s «%s»:\n\n%s" % (kind, path.stem, text)})
    messages.append({"role": "assistant",
                     "content": "Запомнил факты из «%s»." % path.stem})
    used += 1

payload = {
    "conversationId": "hermes-memory-migration-2026-08-06",
    "messages": messages,
    "containerTags": [TAG],
    "metadata": {"sm_source": "hermes", "origin": "migration"},
}

req = urllib.request.Request(
    "https://api.supermemory.ai/v4/conversations",
    data=json.dumps(payload).encode("utf-8"),
    headers={"Authorization": "Bearer %s" % key, "Content-Type": "application/json", "x-sm-source": "hermes"},
    method="POST",
)
try:
    with urllib.request.urlopen(req, timeout=90) as r:
        print("HTTP %s" % r.status)
        print(r.read().decode("utf-8", "replace")[:500])
    print("Залито файлов: %d, сообщений: %d, символов: %d"
          % (used, len(messages), sum(len(m["content"]) for m in messages)))
except urllib.error.HTTPError as e:
    print("ОШИБКА HTTP %s: %s" % (e.code, e.read().decode("utf-8", "replace")[:500]))
