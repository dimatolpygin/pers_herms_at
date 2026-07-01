---
name: content-studio
description: Use whenever the user wants a social-media or marketing post — Russian triggers like «создай пост», «сделай пост», «пост про …», «нужен пост с картинкой/фото», «пост по ссылке» (including Tilda product pages), even without a link and even if they ask for a single version. Produces the post text (usually 3 versions) and generates the post image(s) through kie.ai. This skill OWNS post-image generation — use it instead of the built-in image_generate. For Tilda/product links it also extracts product photos and builds a marketplace product card, then saves the draft to the content table and returns its id.
version: 1.2.0
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

Postgres saving uses, in priority order:

- `CONTENT_DATABASE_URL` first, then `DATABASE_URL`, then `POSTGRES_DSN` (requires psycopg/psycopg2).
- `CONTENT_PSQL_COMMAND` — the **primary Postgres path in this project** (no Python driver needed).
  For local Docker: `docker exec -i postgres16 psql -U postgres -d mydb -At -v ON_ERROR_STOP=1`.
  When this is set, the helper writes to real Postgres through it. Do NOT report "no database /
  database unavailable" just because `CONTENT_DATABASE_URL` or `psycopg` is absent — `CONTENT_PSQL_COMMAND`
  IS the database connection here. Trust the helper's `backend` field, not your own env probing.
- `CONTENT_DB_SCHEMA` optional; default is `hermes_agent`.
- `CONTENT_DB_TABLE` optional; default is `content_drafts`.
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
   - URL/Tilda: call `extract-url` first, then summarize the useful page substance. `extract-url` also
     returns an `images` list — the page's product photos (og:image first; Tilda logos/favicons are
     filtered out). Keep the first one or two image URLs for the image step.
   - If the brief is too sparse to produce a meaningful post, ask one focused follow-up.
   Completion: enough data exists to draft the post.

2. Draft 3 distinct text versions in Russian.
   - Version 1: practical/direct.
   - Version 2: warmer/story-like.
   - Version 3: sales/CTA-focused.
   - Do not use placeholders.
   - Keep platform constraints in mind if the user named a platform.
   Completion: 3 clearly different versions are ready.

3. Choose the image strategy. Two modes:

   **Mode A — marketplace product card from the link (image-to-image).** This is the client's "Вариант 3".
   The post carries **two images, and only the first photo is sent to kie.ai**:
   - **Photo 1 → a marketplace product card** (the single `--input-url` to `gpt-image-2-image-to-image`).
   - **Photo 2 → used as-is**, the raw product photo, with no kie.ai call. Keep its URL/path for the post.
   - The final post = generated card + untouched photo 2. If the page has only one photo, make the card
     from it and skip the second image.

   Build the card from real page data. Read specs out of the `extract-url` text (brand, product type,
   size, material, purpose, handmade, etc.) and compose an infographic listing like Wildberries/Ozon,
   modeled on the client's reference:
   - Keep the real product unchanged on a clean light-gray gradient background with rounded corners.
   - Small brand name at top (e.g. «LOVA CERAMICS»).
   - A very large bold Russian headline = product type (e.g. «ВАЗА») with a smaller subtitle (e.g. «керамическая»).
   - A left column of 3–4 short spec bullets from the page (e.g. «Высота 23 см», «Материал керамика»,
     «Для цветов и сухоцветов», «Ручная работа»).
   - A framed feature badge at the bottom (e.g. «Подходит к любому интерьеру»).
   - All text in Russian; vertical `3:4` or `4:5`. image-to-image renders Cyrillic reliably.

   Prompt skeleton (RU): «Сделай из этого фото карточку товара для маркетплейса в вертикальном формате.
   Сохрани реальный товар без изменений справа, чистый светло-серый градиентный фон со скруглёнными
   углами. Сверху слева мелкий бренд "<БРЕНД>". Под ним очень крупный жирный заголовок "<ТИП ТОВАРА>" и
   помельче "<подзаголовок>". Слева столбцом характеристики: "<п1>", "<п2>", "<п3>", "<п4>". Внизу слева
   бейдж в рамке "<преимущество>". Все надписи на русском, чёткие, премиальный минимализм.»

   **Mode B — cover from scratch (text-to-image).** Use for free-form briefs with no usable product
   photo. Build a YouTube-thumbnail-style cover with on-image Russian text. Wrap your concrete visual
   brief in this exact template — keep the wording, replace only the inner `...`:

   ```
   создать обложку для - "<краткое описание сцены/темы + главный заголовок 2–4 слова>". стиль ютуб обложка. текста на обложке русские. надо учесть что обложка будет отображаться маленькой, поэтому сделай крупный читаемый текст, 2–4 слова максимум, без мелких деталей. Главный текст должен читаться даже при уменьшении до 160 px по ширине.
   ```

   - Choose a punchy 2–4 word Russian headline that matches the post and name it inside the brief.
   - Keep the scene bold and simple: one clear subject, high contrast, no fine detail.
   - Aspect ratio: default `16:9` for the cover look; use `1:1` for a square feed post or `9:16` for stories/reels.
   Completion: Mode A has 1–2 product photo URLs ready, or Mode B has a cover prompt with a clear headline.

4. Generate the image(s) through kie.ai.
   - Mode A: `generate-image --prompt "<card prompt with specs>" --input-url <photo1> --aspect-ratio 3:4`.
     Only photo 1 goes to kie.ai (the card). Photo 2 stays raw — do NOT send it. The post uses both.
   - Mode B: `generate-image --prompt "<cover prompt>"` or `prepare --generate-image` (text-to-image).
   - The helper calls `createTask`, polls `recordInfo`, downloads the first result URL, and reports the `model`.
   Completion: result JSON has `image_url` and preferably `image_path` for each image.

5. Preview in chat.
   - Show the 3 versions as numbered options.
   - Include every post image. On Telegram, add one `MEDIA:<absolute image_path>` line per image — in
     Mode A that is the generated card first, then the raw photo 2. Use `image_path` when present.
   - Ask which version to save/publish later if the user has not selected one.
   Completion: the user can see the options and all post images.

6. Save the draft.
   - Put the full ordered post gallery in `images` (Mode A: `[{role:"card",...},{role:"raw",url:photo2}]`).
     Keep the primary `image_url`/`image_path` = the main/card image.
   - If the user already selected a version, set `selected_version`.
   - If not, save `selected_version: 1` only when the user asked to save immediately; otherwise ask.
   - Run `save-draft` or `prepare --save` **with `--require-postgres`** for this project, so the
     helper never silently degrades to the JSONL fallback. If the save fails, surface the real error
     instead of switching to JSONL on your own.
   - Read `backend` and `id` from the helper's JSON output and report them as-is. `backend: "postgres"`
     means it is saved in the database (whether via DSN or `CONTENT_PSQL_COMMAND`). Never announce
     "база недоступна / JSONL" unless the helper actually returned `backend: "jsonl-fallback"`.
   Completion: final answer includes the saved draft id and the real backend.

## Helper Commands

URL extraction (returns text + `images` = product photos, og:image first):

```bash
python scripts/content_studio.py extract-url "https://example.com/page" --max-images 4
```

Image generation — Mode B, text-to-image cover:

```bash
python scripts/content_studio.py generate-image --prompt "..." --aspect-ratio 16:9
```

Image edit — Mode A, image-to-image (marketplace product card from photo 1):

```bash
python scripts/content_studio.py generate-image \
  --prompt "Карточка товара для маркетплейса: сохрани реальный товар, светло-серый градиентный фон со скруглёнными углами, бренд \"LOVA CERAMICS\", крупный заголовок \"ВАЗА\" / \"керамическая\", характеристики слева \"Высота 23 см\", \"Материал керамика\", \"Для цветов и сухоцветов\", \"Ручная работа\", бейдж \"Подходит к любому интерьеру\"" \
  --input-url "https://static.tildacdn.com/stor.../photo1.jpg" --aspect-ratio 3:4
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
    "image_prompt": "создать обложку для - \"ремонт квартиры под ключ, светлая комната, главный заголовок 'РЕМОНТ ПОД КЛЮЧ'\". стиль ютуб обложка. текста на обложке русские. надо учесть что обложка будет отображаться маленькой, поэтому сделай крупный читаемый текст, 2–4 слова максимум, без мелких деталей. Главный текст должен читаться даже при уменьшении до 160 px по ширине.",
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
  "images": [
    {"role": "card", "url": "https://.../card.png", "path": "C:\\Users\\...\\card.png"},
    {"role": "raw", "url": "https://static.tildacdn.com/stor.../photo2.jpg"}
  ],
  "platforms": ["instagram"],
  "scheduled_at": null
}
```

`image_url`/`image_path` may be filled by `prepare --generate-image`. `images` is the ordered post
gallery (Mode A: generated card + raw photo 2); when omitted it defaults to the single primary image.

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

6. Inventing a picture for a product link.
   Fix: when `extract-url` returned `images`, prefer Mode A (image-to-image on the real product photo)
   over Mode B (cover from scratch). The client wants the actual product, background extended.

## Verification Checklist

- [ ] Free-form brief produces 3 distinct Russian text versions.
- [ ] URL/Tilda extraction returns page title/description/text and the final texts reflect the page.
- [ ] URL/Tilda extraction returns `images` (product photos, no logos/favicons).
- [ ] Mode A: photo 1 → marketplace card (brand, big headline, 3–4 real spec bullets, feature badge), photo 2 kept raw; only photo 1 sent to kie.ai.
- [ ] Mode B: text-to-image cover returns an image URL and the helper downloads a local file.
- [ ] `save-draft --require-postgres` returns `backend: "postgres"` and an integer draft id.
- [ ] Telegram preview includes 3 texts and the generated image.
