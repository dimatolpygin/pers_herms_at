---
name: business-documents
description: Используй, когда пользователь просит сделать коммерческое предложение/КП в .docx или табличный отчёт в .csv и ждёт готовый файл вложением, особенно в Telegram. Составь реальное содержание, сгенерируй файл прилагаемым скриптом, затем верни абсолютный путь к файлу или тег MEDIA:.
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
- Readable business reports as Excel-friendly `.csv` files with a document-style layout:
  title, spacer rows, sections, paragraphs, bullet lists, and embedded tables.

Use the bundled `scripts/business_docs.py` helper. It has no third-party dependencies: `.docx`
is generated as a minimal Office Open XML package and `.csv` is written as UTF-8 with BOM. For
section-based reports the default delimiter is comma, matching spreadsheet-export CSV samples;
flat legacy `columns`/`rows` reports default to semicolon.

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
   - report / Excel-ready summary -> `report` -> `.csv`.
   Completion: one output kind is selected.

2. Draft real content from the user's request before running the script.
   - Do not create empty placeholders.
   - If a detail is missing but non-critical, make a reasonable assumption and include it in the document.
   - Ask a follow-up only when the missing data changes the document's purpose or recipient.
   - For CSV reports, prefer `sections` over a single flat `columns`/`rows` table. A good CSV report
     should look like the reference product report: title row, blank separator rows, section headings,
     bullet lists, and compact tables inside sections.
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

Recommended section-based JSON:

```json
{
  "title": "Подробный отчёт по проекту Hermes Agent",
  "summary": "Краткая вводная строка отчёта.",
  "sections": [
    {
      "heading": "Обзор",
      "paragraphs": [
        "Hermes Agent настраивается как персональный Telegram-агент с памятью, голосом и генерацией документов."
      ],
      "bullets": ["Текст и голос уже работают", "Память подключена", "Документы генерируются файлами"]
    },
    {
      "heading": "Статус этапов",
      "table": {
        "columns": ["Этап", "Статус", "Проверка"],
        "rows": [
          ["0 Каркас", "закрыт", "stage-0-done"],
          ["1 Живой мозг", "закрыт", "stage-1-done"],
          ["2 Память", "закрыт", "stage-2-done"],
          ["3 Docx/csv", "в работе", "ждёт Telegram UAT"]
        ]
      }
    },
    {
      "heading": "Важные замечания",
      "bullets": [
        "CSV не хранит цвета, шрифты и ширины колонок; красота достигается структурой строк.",
        "Для настоящего визуального оформления нужен .xlsx или .docx."
      ]
    }
  ]
}
```

Command:

```bash
python scripts/business_docs.py report --spec report.json
```

Flat legacy JSON is still supported:

```json
{
  "title": "Отчёт по задачам",
  "columns": ["Задача", "Статус", "Комментарий"],
  "rows": [
    ["Каркас Hermes", "Готово", "Бот отвечает в Telegram"],
    ["Docx/csv", "В работе", "Добавляется навык генерации файлов"]
  ]
}
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

3. Expecting CSV to preserve colors, fonts, merged cells, or column widths.
   Fix: CSV cannot store visual formatting. Use section layout for readable CSV, or switch the user to `.xlsx`/`.docx` if true visual styling is required.

4. Writing every report as one flat table.
   Fix: for product, annual, analytical, or research reports, use `sections` with headings, bullets, and embedded tables.

5. Forgetting that CLI cannot upload attachments.
   Fix: in CLI/TUI, print the absolute path plainly instead of `MEDIA:`.

## Verification Checklist

- [ ] `.docx` exists, is a zip package, and contains `word/document.xml`.
- [ ] `.csv` exists, opens as text with UTF-8 BOM, and has the requested columns/rows.
- [ ] Final Telegram response includes `MEDIA:<absolute path>` for each deliverable file.
- [ ] The content reflects the user's request and is not a placeholder.
