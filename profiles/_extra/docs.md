## Как звать критика

Штатного тула для критика в этом профиле нет — вызов делается напрямую в OpenRouter.
Это осознанный временный путь: правильное место для критик-цикла — `kanban swarm`
(workers -> verifier -> synthesizer), но он в оплаченный объём не входит. Сниппет ниже
работает и проверен на приёмке этапа 19 — не переписывать его «покрасивее» без замены
на swarm целиком.

Код:
```python
import json, os, urllib.request

api_key = os.environ.get("OPENROUTER_API_KEY")
if not api_key:
    with open(os.path.expanduser("~/.hermes/profiles/docs/.env")) as f:
        for line in f:
            if line.startswith("OPENROUTER_API_KEY="):
                api_key = line.strip().split("=", 1)[1]
                break

payload = json.dumps({
    "model": "qwen/qwen3.7-max",
    "messages": [{"role": "user", "content": f"""Ты финансовый контролёр. Проверь документ LOVA:

1. Реквизиты: ИНН 771411755860, ОГРН 316774600375557, Р/с 40802810101380002412 АО «АЛЬФА-БАНК»?
2. Суммы: арифметика верна?
3. УСН 6% (доходы), без НДС?
4. Структура таблиц по образцу?
5. Нет лишних колонок?

Ответь PASS или FAIL с конкретными ошибками.

Документ: {doc_text}"""}],
    "max_tokens": 1200
}).encode()

req = urllib.request.Request(
    "https://openrouter.ai/api/v1/chat/completions",
    data=payload,
    headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
)
with urllib.request.urlopen(req, timeout=30) as resp:
    print(json.loads(resp.read())["choices"][0]["message"]["content"])
```

## Реквизиты
ИП Миронов Михаил Юрьевич
ИНН 771411755860, ОГРН 316774600375557
Р/с 40802810101380002412 в АО «АЛЬФА-БАНК»
УСН 6% (доходы), НДС не облагается

## Оформление
Логотип LOVA — левый верхний угол .docx
Файл: /root/.hermes/assets/lova_logo.png
→ Правила: /root/.hermes/notes/lova-invoice.md
