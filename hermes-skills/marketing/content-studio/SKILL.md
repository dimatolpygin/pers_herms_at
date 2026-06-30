---
name: content-studio
description: Use when the user asks to prepare a social-media post, ad caption, promo content, or content draft from a free-form brief or URL/Tilda page, with 3 text versions, a generated image through kie.ai, and a saved content draft id.
version: 1.1.0
author: Hermes Agent Project
license: MIT
required_environment_variables:
  - name: KIE_AI_API_KEY
    optional: true
    prompt: kie.ai API key for gpt-image-2 generation
    help: https://kie.ai
  - name: CONTENT_PSQL_COMMAND
    optional: true
    prompt: psql command for saving drafts to local Postgres
  - name: CONTENT_DATABASE_URL
    optional: true
    prompt: Postgres DSN for content drafts (alternative to CONTENT_PSQL_COMMAND)
  - name: CONTENT_DB_SCHEMA
    optional: true
  - name: CONTENT_DB_TABLE
    optional: true
metadata:
  hermes:
    tags: [content, social-media, kie-ai, postgres, telegram]
    related_skills: [social-media, business-documents]
---

# Content Studio

## Overview

This skill prepares social content drafts for the Hermes client workflow:

- Accepts a free-form brief or a URL, including Tilda pages.
- Produces 3 distinct text versions for the post.
- Generates a visual through kie.ai `gpt-image-2-text-to-image`.
- Shows a preview in chat: 3 text versions + image.
- Saves the selected draft into the content table and returns the draft id.

Use the bundled `scripts/content_studio.py` helper for external operations. The LLM should draft
the actual post text before calling the helper.

## Environment

Image generation requires one of:

- `KIE_AI_API_KEY`
- `KIE_API_KEY`
- `KIE_TOKEN`

Postgres saving uses:

- `CONTENT_DATABASE_URL` first, then `DATABASE_URL`, then `POSTGRES_DSN`.
- `CONTENT_DB_SCHEMA` optional; default is `hermes_agent`.
- `CONTENT_DB_TABLE` optional; default is `content_drafts`.
- `CONTENT_PSQL_COMMAND` optional fallback when Python has no Postgres driver. For local Docker:
  `docker exec -i postgres16 psql -U postgres -d mydb -At -v ON_ERROR_STOP=1`.
- `CONTENT_STUDIO_ARTIFACT_DIR` optional; overrides the local artifact directory for images and JSONL fallback.

The project migration creates `hermes_agent.content_drafts`. Use a dedicated schema because the
local Docker Postgres database is shared with other projects.

These variables are declared in this skill's `required_environment_variables` frontmatter so they
pass through to the `execute_code` / terminal sandboxes (otherwise Hermes strips any name
containing `KEY`/`DSN`/`TOKEN`). Two operational rules follow from that:

- The values are read from the running gateway's environment, which is loaded from
  `%LOCALAPPDATA%\hermes\.env` **at startup**. After editing `.env`, restart the gateway — a
  stale gateway will not expose `KIE_AI_API_KEY` to the sandbox even though the skill declares it.
- Passthrough only registers a variable that is actually set; missing ones are simply skipped.

If no Postgres DSN or `CONTENT_PSQL_COMMAND` is configured, the helper writes a local JSONL
fallback under Hermes artifacts. This is only for dev flow checks; stage 4 acceptance requires a
real Postgres row id.

## When to Use

Use this skill when the user asks:

- "Сделай пост про ...", "подготовь контент", "придумай 3 варианта поста".
- "Сделай пост по ссылке", including Tilda pages.
- "Нужен текст + картинка", "сгенерируй картинку к посту".
- "Сохрани черновик", "выбери вариант и сохрани".

Do not use this skill to publish posts. Publication through PostMyPost belongs to the later
publication skill/stage.

## Workflow

1. Understand the source.
   - Free-form brief: extract topic, product/service, target audience, offer, tone, platform, and CTA.
   - URL/Tilda: call `extract-url` first, then summarize the useful page substance.
   - If the brief is too sparse to produce a meaningful post, ask one focused follow-up.
   Completion: enough data exists to draft the post.

2. Draft 3 distinct text versions in Russian.
   - Version 1: practical/direct.
   - Version 2: warmer/story-like.
   - Version 3: sales/CTA-focused.
   - Do not use placeholders.
   - Keep platform constraints in mind if the user named a platform.
   Completion: 3 clearly different versions are ready.

3. Build an image prompt.
   - Use the business/topic, mood, visual scene, format, and "no text overlays" unless the user asked for text.
   - Default aspect ratio: `1:1`. Use `9:16` for stories/reels, `16:9` for wide preview.
   Completion: prompt is specific enough for a useful commercial visual.

4. Generate the image through kie.ai.
   - Run `generate-image` or `prepare --generate-image`.
   - The helper calls `createTask`, polls `recordInfo`, and downloads the first result URL.
   Completion: result JSON has `image_url` and preferably `image_path`.

5. Preview in chat.
   - Show the 3 versions as numbered options.
   - Include the generated image. On Telegram, add `MEDIA:<absolute image_path>` when `image_path` exists.
   - Ask which version to save/publish later if the user has not selected one.
   Completion: the user can see the options and image.

6. Save the draft.
   - If the user already selected a version, set `selected_version`.
   - If not, save `selected_version: 1` only when the user asked to save immediately; otherwise ask.
   - Run `save-draft` or `prepare --save`.
   Completion: final answer includes the saved draft id.

## Helper Commands

URL extraction:

```bash
python scripts/content_studio.py extract-url "https://example.com/page"
```

Image generation:

```bash
python scripts/content_studio.py generate-image --prompt "..." --aspect-ratio 1:1
```

Save a prepared draft:

```bash
python scripts/content_studio.py save-draft --spec draft.json --require-postgres
```

Combined image + save:

```bash
python scripts/content_studio.py prepare --spec draft.json --generate-image --save --require-postgres
```

## Hermes execute_code Recipe

Prefer `execute_code` in Hermes chat. It avoids shell approval friction and loads the helper from
the installed skill directory.

```python
import importlib.util, json, os
from pathlib import Path

root = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "hermes"
script = root / "skills" / "marketing" / "content-studio" / "scripts" / "content_studio.py"
artifacts = root / "artifacts" / "content-studio"
artifacts.mkdir(parents=True, exist_ok=True)

draft = {
    "source_type": "freeform",
    "source_value": "Сообщение пользователя в Telegram",
    "topic": "Новый ремонтный сервис",
    "product_name": "Сервис ремонта квартир",
    "raw_input": {"brief": "Сделай пост про ремонт под ключ"},
    "text_versions": [
        {"angle": "direct", "text": "Первый готовый вариант текста..."},
        {"angle": "warm", "text": "Второй готовый вариант текста..."},
        {"angle": "sales", "text": "Третий готовый вариант текста..."}
    ],
    "selected_version": 1,
    "image_prompt": "Modern clean social media visual for apartment renovation, bright room, no text overlays.",
    "platforms": ["instagram"]
}
spec_path = artifacts / "draft_spec.json"
spec_path.write_text(json.dumps(draft, ensure_ascii=False), encoding="utf-8")

module_spec = importlib.util.spec_from_file_location("content_studio", script)
module = importlib.util.module_from_spec(module_spec)
module_spec.loader.exec_module(module)
module.main(["prepare", "--spec", str(spec_path), "--generate-image", "--save", "--require-postgres"])
```

## Draft Spec

Required fields:

```json
{
  "topic": "Продвижение услуги ремонта под ключ",
  "source_type": "freeform",
  "source_value": "исходный запрос или URL",
  "raw_input": {
    "brief": "пользовательский ввод или краткая выжимка страницы"
  },
  "text_versions": [
    {
      "angle": "direct",
      "text": "Готовый вариант текста поста."
    },
    {
      "angle": "warm",
      "text": "Второй вариант текста поста."
    },
    {
      "angle": "sales",
      "text": "Третий вариант текста поста."
    }
  ],
  "selected_version": 1,
  "image_prompt": "Prompt for kie.ai",
  "image_url": "https://...",
  "image_path": "C:\\Users\\...\\image.png",
  "platforms": ["instagram"],
  "scheduled_at": null
}
```

`image_url`/`image_path` may be filled by `prepare --generate-image`.

## Output Contract

The helper prints JSON. For image generation:

```json
{
  "ok": true,
  "task_id": "kie task id",
  "image_url": "https://...",
  "image_path": "C:\\Users\\...\\image.png"
}
```

For saving:

```json
{
  "ok": true,
  "backend": "postgres",
  "schema": "hermes_agent",
  "table": "content_drafts",
  "id": 12,
  "status": "draft"
}
```

Use the exact `id`, `image_url`, and `image_path` returned by the helper.

## Common Pitfalls

1. Saving generic text.
   Fix: write 3 real, distinct post versions before calling `save-draft`.

2. Writing into `public.content_drafts` by accident.
   Fix: use `CONTENT_DB_SCHEMA=hermes_agent` or the agreed project schema.

3. Treating JSONL fallback as stage acceptance.
   Fix: fallback is only for local flow checks. Real acceptance needs `backend: "postgres"` and a DB id.

4. Returning the image path in backticks on Telegram.
   Fix: for Telegram final preview, use `MEDIA:<absolute image_path>` without backticks.

5. Publishing immediately.
   Fix: stage 4 only prepares and saves drafts. PostMyPost publication is stage 5.

## Verification Checklist

- [ ] Free-form brief produces 3 distinct Russian text versions.
- [ ] URL/Tilda extraction returns page title/description/text and the final texts reflect the page.
- [ ] kie.ai returns an image URL and the helper downloads a local image file.
- [ ] `save-draft --require-postgres` returns `backend: "postgres"` and an integer draft id.
- [ ] Telegram preview includes 3 texts and the generated image.
