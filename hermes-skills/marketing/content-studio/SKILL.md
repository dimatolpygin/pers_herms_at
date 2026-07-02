---
name: content-studio
description: Используй, когда пользователь хочет пост для соцсетей или маркетинговый пост — триггеры «создай пост», «сделай пост», «пост про …», «нужен пост с картинкой/фото», «пост по ссылке» (включая товарные страницы Tilda), даже без ссылки и даже если просят один вариант. Создаёт текст поста (обычно 3 версии) и подбирает картинки. Для ссылок Tilda/товаров берёт первые до 2 фото с сайта КАК ЕСТЬ, без генерации и без карточки. Без ссылки всегда спрашивает «картинку свою скинете или сгенерировать?» и рисует обложку через kie.ai ТОЛЬКО если пользователь выбрал «сгенерировать». Этот навык ОТВЕЧАЕТ за картинки постов — используй его вместо встроенного image_generate. Затем сохраняет черновик в таблицу контента и возвращает его id.
version: 1.4.0
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
- Images: for a product link, uses the **first up to 2 site photos as-is** (no generation, no card);
  with no link, asks the user **«картинку свою скинете или сгенерировать?»** and only generates a cover
  via kie.ai `gpt-image-2-text-to-image` when the user chooses to generate.
- Shows a preview in chat: 3 text versions + image(s).
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

1. Understand the source AND the target.
   - **Ask WHERE and WHEN before drafting** (unless the user already said): which social networks
     (client has Instagram, Telegram, VK, Pinterest) and what date/time in **МСК / Moscow time**
     (or «сейчас»). Knowing the platform up front shapes the text — Pinterest wants a title + link,
     Telegram tolerates longer copy, Instagram needs an image. Keep this to one short question.
   - **Pick platforms in two separate steps, not one bundled question.**
     - Step 1: ask where to post — offer **Telegram, VK, Instagram, Pinterest** or **«все сразу»**;
       the user may select several. Do NOT default to all platforms — cross-post everywhere only if
       the user explicitly picks «все сразу».
     - Step 2 (only if Pinterest is among the picks): ask, as a **separate** question, which board —
       4 options: «Вазы и кашпо напольные», «Авторские лампы и торшеры», «Мастерская авторской керамики
       LOVA CERAMICS», «Products» (you may suggest the one matching the product). Do not fold the
       Pinterest boards into the step-1 network list.
     - The exact account names/ids come from the `postmypost` skill (`accounts`).
   - Free-form brief: extract topic, product/service, target audience, offer, tone, platform, and CTA.
   - URL/Tilda: call `extract-url` first, then summarize the useful page substance. `extract-url` also
     returns an `images` list — the page's product photos (og:image first; Tilda logos/favicons are
     filtered out). Keep the first one or two image URLs for the image step.
   - If the brief is too sparse to produce a meaningful post, fold the missing bits into that same
     follow-up question.
   Completion: enough data exists to draft the post, and target `platforms` + timing (МСК, or «сейчас») are known.

2. Draft 3 distinct text versions in Russian.
   - Version 1: practical/direct.
   - Version 2: warmer/story-like.
   - Version 3: sales/CTA-focused.
   - Do not use placeholders.
   - Keep platform constraints in mind if the user named a platform.
   Completion: 3 clearly different versions are ready.

3. Choose the image(s). The client's rule has two cases — decide strictly by whether a product link was given:

   **Case A — the user gave a product link (Tilda / website): use the site's OWN photos AS-IS.**
   This is the client's "Вариант 3". **No generation, no kie.ai, no marketplace card here.**
   - Take the **first up to 2 photos** from `extract-url`'s `images` list, in page order, unchanged.
   - If the page has 4 photos → use the first 2. If it has 3 → first 2. If it has only 1 → use that 1.
     If it has 0 usable photos (all filtered as logos/favicons) → fall back to Case B (ask the user).
   - Each chosen photo goes into the post as a **raw** image (`role:"raw"`), keeping page order. Do NOT
     edit, crop, or send them to kie.ai — they are the final post images as-is.

   **Case B — no link / unclear where the image should come from: ALWAYS ask the user first.**
   Ask exactly one short question and wait for the answer:

   > **«Картинку свою скинете или сгенерировать?»**

   - If the user **sends their own photo** → upload it with the `s3-upload` skill and use the returned
     public URL as a **raw** image (`role:"raw"`). Do NOT call kie.ai.
   - If the user says **«сгенерировать» / «генерируй»** → this is the **only** case kie.ai is used.
     Generate a YouTube-thumbnail-style cover via kie.ai **text-to-image**. Wrap your concrete visual
     brief in this exact template — keep the wording, replace only the inner `...`:

     ```
     создать обложку для - "<краткое описание сцены/темы + главный заголовок 2–4 слова>". стиль ютуб обложка. текста на обложке русские. надо учесть что обложка будет отображаться маленькой, поэтому сделай крупный читаемый текст, 2–4 слова максимум, без мелких деталей. Главный текст должен читаться даже при уменьшении до 160 px по ширине.
     ```

     - Choose a punchy 2–4 word Russian headline that matches the post and name it inside the brief.
     - Keep the scene bold and simple: one clear subject, high contrast, no fine detail.
     - Aspect ratio: default `16:9`; use `1:1` for a square feed post or `9:16` for stories/reels.

   Completion: Case A has 1–2 raw site-photo URLs; Case B has either the user's uploaded photo URL or a
   generated cover. kie.ai is touched ONLY in Case B when the user explicitly chose «сгенерировать».

4. Prepare the image(s).
   - **Case A (link): nothing to generate.** Keep the 1–2 raw site-photo URLs from step 3 as-is.
   - **Case B, user photo:** upload it via the `s3-upload` skill and keep the returned public URL. No generation.
   - **Case B, «сгенерировать»:** `generate-image --prompt "<cover prompt>"` or `prepare --generate-image`
     (text-to-image). The helper calls `createTask`, polls `recordInfo`, downloads the first result URL,
     and reports the `model`. This is the only kie.ai call in the whole skill.
   Completion: every post image has a URL (and, for a generated cover, preferably a local `image_path`).

5. Preview in chat.
   - Show the 3 versions as numbered options.
   - Include every post image. On Telegram, add one `MEDIA:<absolute image_path or URL>` line per image —
     Case A: the 1–2 raw site photos in page order; Case B: the user's photo or the generated cover.
   - Ask which version to save/publish later if the user has not selected one.
   Completion: the user can see the options and all post images.

6. Save the draft.
   - Put the full ordered post gallery in `images` (Case A: `[{role:"raw",url:photo1},{role:"raw",url:photo2}]`,
     the first 1–2 site photos in page order; Case B: `[{role:"raw",...}]` user photo, or `[{role:"card",...}]`
     generated cover). Keep the primary `image_url`/`image_path` = the first image.
   - Save the answers from step 1 in a **cron-ready** shape:
     - `platforms` must carry the concrete **PostMyPost account ids**, not bare network names —
       stage 6 posts by cron with NO LLM, so the row itself has to say exactly where to publish.
       Resolve the chosen networks/board to account ids via the `postmypost` skill (`accounts`) and
       store each as an object: `{"network": "pinterest", "account_id": 2180768, "name": "Вазы и кашпо напольные"}`.
       `["pinterest"]` (a plain name) is NOT enough — a Pinterest name is ambiguous across 4 boards.
     - `scheduled_at` as an ISO datetime in **МСК** (`+03:00`, e.g. `2026-07-02T10:00:00+03:00`). For
       «сейчас» leave `scheduled_at` null — the publish step posts immediately.
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

Image generation — Case B only (used solely when the user chose «сгенерировать»), text-to-image cover:

```bash
python scripts/content_studio.py generate-image --prompt "..." --aspect-ratio 16:9
```

Product link (Case A): **no image command at all** — `extract-url` already returns the site photos;
take the first up to 2 URLs from its `images` list and use them raw. Do not call `generate-image`.
(The helper still supports `generate-image --input-url` for image-to-image, but the client's current
policy does NOT use marketplace-card generation — links use the real site photos as-is.)

Save a prepared draft:

```bash
python scripts/content_studio.py save-draft --spec draft.json --require-postgres
```

Update an existing draft in place — the publish write-back (status, time, publication links). Always
UPDATE the same row by id instead of saving a new one:

```bash
python scripts/content_studio.py update-draft --id 16 \
  --status published --scheduled-at "2026-07-02T10:00:00+03:00" \
  --publication-links '[{"id":30811163,"network":"instagram","account_id":2180775}]' --append-links
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
    "platforms": [{"network": "instagram", "account_id": 2180775, "name": "lova_ceramics"}]
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
    {"role": "raw", "url": "https://static.tildacdn.com/stor.../photo1.jpg"},
    {"role": "raw", "url": "https://static.tildacdn.com/stor.../photo2.jpg"}
  ],
  "platforms": [
    {"network": "instagram", "account_id": 2180775, "name": "lova_ceramics"},
    {"network": "pinterest", "account_id": 2180768, "name": "Вазы и кашпо напольные"}
  ],
  "scheduled_at": null
}
```

`image_url`/`image_path` may be filled by `prepare --generate-image` (Case B generate only). `images`
is the ordered post gallery (Case A: the first 1–2 raw site photos; Case B: the user's photo or the
generated cover); when omitted it defaults to the single primary image.

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

6. Generating anything for a product link.
   Fix: for a link, use the **first up to 2 real site photos as-is** (Case A) — never generate, never
   build a marketplace card, never call kie.ai. kie.ai is only for Case B when the user explicitly
   chose «сгенерировать».

## Verification Checklist

- [ ] Free-form brief produces 3 distinct Russian text versions.
- [ ] URL/Tilda extraction returns page title/description/text and the final texts reflect the page.
- [ ] URL/Tilda extraction returns `images` (product photos, no logos/favicons).
- [ ] Case A (link): first up to 2 site photos used as-is (4→2, 1→1); nothing sent to kie.ai; no card.
- [ ] Case B (no link): agent asks «картинку свою скинете или сгенерировать?»; user photo → s3-upload raw; «сгенерировать» → text-to-image cover.
- [ ] `save-draft --require-postgres` returns `backend: "postgres"` and an integer draft id.
- [ ] Telegram preview includes 3 texts and the generated image.
