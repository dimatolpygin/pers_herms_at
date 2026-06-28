---
name: business-documents
description: Use when the user asks to create a commercial proposal/KP in .docx or a tabular report in .csv and expects a ready file attachment, especially in Telegram. Draft the real content, generate the file via the bundled script, then return the absolute path or MEDIA: tag.
version: 1.0.0
author: Hermes Agent Project
license: MIT
metadata:
  hermes:
    tags: [documents, docx, csv, business, telegram]
    related_skills: [ocr-and-documents, powerpoint]
---

# Business Documents

## Overview

This skill produces two deliverables for the Hermes client workflow:

- Commercial proposals / КП as valid `.docx` files.
- Simple business reports as Excel-friendly `.csv` files.

Use the bundled `scripts/business_docs.py` helper. It has no third-party dependencies: `.docx`
is generated as a minimal Office Open XML package and `.csv` is written as UTF-8 with BOM and
semicolon delimiters for Russian Excel compatibility.

## When to Use

Use this skill when the user asks for:

- "Сделай КП...", "коммерческое предложение", "proposal", or a Word `.docx` business document.
- "Сделай отчёт-таблицу...", "сводку в CSV", "таблицу для Excel", or a `.csv` report.
- A deliverable file, not just text pasted into chat.

Do not use this skill for slide decks (`powerpoint`), OCR/extraction from existing PDFs
(`ocr-and-documents`), or long designed PDFs.

## Workflow

1. Decide the deliverable:
   - КП / commercial proposal -> `proposal` -> `.docx`.
   - report table / Excel-ready summary -> `report` -> `.csv`.
   Completion: one output kind is selected.

2. Draft real content from the user's request before running the script.
   - Do not create empty placeholders.
   - If a detail is missing but non-critical, make a reasonable assumption and include it in the document.
   - Ask a follow-up only when the missing data changes the document's purpose or recipient.
   Completion: the spec has enough concrete text to stand on its own.

3. Write a UTF-8 JSON spec file and run the helper script.
   - In Hermes chat, prefer `execute_code` over terminal commands. The terminal tool may require
     approval for local commands; `execute_code` can run the standard-library helper directly.
   - Windows installed path:
     `%LOCALAPPDATA%\hermes\skills\productivity\business-documents\scripts\business_docs.py`
   - Cross-platform installed path:
     `~/.hermes/skills/productivity/business-documents/scripts/business_docs.py`
   - If this skill is loaded from another directory, resolve `scripts/business_docs.py` relative to this `SKILL.md`.
   Completion: the script prints JSON with `"ok": true` and an absolute `"path"`.

4. Deliver the file.
   - On Telegram/Discord/Slack/WhatsApp/etc., include `MEDIA:<absolute path>` in the final answer.
   - On CLI/TUI, do not use `MEDIA:`; print the absolute path plainly.
   Completion: the final answer contains the correct path for the current platform.

## Proposal Spec

Minimal JSON:

```json
{
  "title": "Коммерческое предложение",
  "client": "ООО Клиент",
  "subject": "Внедрение персонального ИИ-агента",
  "summary": "Короткое описание сути предложения.",
  "sections": [
    {
      "heading": "Что входит",
      "paragraphs": [
        "Настройка Telegram-бота, памяти, голосового ввода и генерации документов."
      ]
    }
  ],
  "pricing": [
    {
      "item": "Внедрение агента",
      "qty": "1 проект",
      "price": "25 000 ₽",
      "total": "25 000 ₽"
    }
  ],
  "terms": ["Срок: 7-10 рабочих дней.", "Оплата: 50/50."],
  "contacts": ["Исполнитель: Анастасия"]
}
```

Command:

```bash
python scripts/business_docs.py proposal --spec proposal.json
```

Hermes `execute_code` recipe:

```python
import importlib.util, json, os
from pathlib import Path

spec = {
    "title": "Коммерческое предложение",
    "client": "ООО Клиент",
    "subject": "Внедрение персонального ИИ-агента",
    "summary": "Короткое описание сути предложения.",
    "sections": [{"heading": "Что входит", "paragraphs": ["Настройка Telegram-бота, памяти и документов."]}],
    "pricing": [{"item": "Внедрение агента", "qty": "1 проект", "price": "25 000 ₽", "total": "25 000 ₽"}],
    "terms": ["Срок: 7-10 рабочих дней."]
}

root = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "hermes"
script = root / "skills" / "productivity" / "business-documents" / "scripts" / "business_docs.py"
spec_path = root / "artifacts" / "business-documents" / "proposal_spec.json"
spec_path.parent.mkdir(parents=True, exist_ok=True)
spec_path.write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")

module_spec = importlib.util.spec_from_file_location("business_docs", script)
module = importlib.util.module_from_spec(module_spec)
module_spec.loader.exec_module(module)
module.main(["proposal", "--spec", str(spec_path)])
```

## Report Spec

Minimal JSON:

```json
{
  "title": "Отчёт по задачам",
  "columns": ["Задача", "Статус", "Комментарий"],
  "rows": [
    ["Каркас Hermes", "Готово", "Бот отвечает в Telegram"],
    ["Docx/csv", "В работе", "Добавляется навык генерации файлов"]
  ],
  "notes": ["CSV записывается в UTF-8 with BOM; разделитель - точка с запятой."]
}
```

Command:

```bash
python scripts/business_docs.py report --spec report.json
```

## Output Contract

The helper prints a JSON object:

```json
{
  "ok": true,
  "kind": "proposal",
  "path": "C:\\Users\\...\\kp_ai_agent_20260628_223500.docx",
  "bytes": 12345
}
```

Use the exact `path` value for delivery. Do not invent or restate a different path.

## Common Pitfalls

1. Creating a document with generic filler.
   Fix: draft user-specific sections and pricing/rows first; if assumptions were needed, state them inside the file.

2. Returning the path inside backticks on Telegram.
   Fix: use `MEDIA:<absolute path>` without backticks so the gateway uploads it.

3. Writing comma CSV for Russian Excel.
   Fix: let the helper write semicolon-delimited UTF-8 BOM CSV.

4. Forgetting that CLI cannot upload attachments.
   Fix: in CLI/TUI, print the absolute path plainly instead of `MEDIA:`.

## Verification Checklist

- [ ] `.docx` exists, is a zip package, and contains `word/document.xml`.
- [ ] `.csv` exists, opens as text with UTF-8 BOM, and has the requested columns/rows.
- [ ] Final Telegram response includes `MEDIA:<absolute path>` for each deliverable file.
- [ ] The content reflects the user's request and is not a placeholder.
