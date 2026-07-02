---
name: postmypost-publish
description: Используй, когда пользователь хочет опубликовать или запланировать готовый пост в соцсети — триггеры «запости», «опубликуй», «выложи в соцсети», «постни», «запланируй пост», «отправь в инсту/телеграм/вк/пинтерест». Берёт черновик контента (текст + картинку/карточку из content-studio), спрашивает КУДА (в какие из подключённых аккаунтов) и КОГДА (сейчас или на запланированное время), затем публикует через PostMyPost. По умолчанию создаёт ЧЕРНОВИК; живая публикация требует явного подтверждения пользователя.
version: 1.1.0
author: Hermes Agent Project
license: MIT
required_environment_variables:
  - name: POSTMYPOST_TOKEN
    optional: true
    prompt: PostMyPost Bearer token
    help: https://help.postmypost.io/ru/docs/api/
  - name: POSTMYPOST_PROJECT_ID
    optional: true
    prompt: PostMyPost project id
metadata:
  hermes:
    tags: [social-media, publishing, postmypost, content]
    related_skills: [content-studio]
---

# PostMyPost Publish

## Overview

Publishes a prepared post (from `content-studio`) to the client's social networks through
PostMyPost. Flow: pick accounts → pick time (now / scheduled) → upload images → create publication.

Use the bundled `scripts/postmypost.py` helper. Credentials come from `POSTMYPOST_TOKEN` and
`POSTMYPOST_PROJECT_ID` (declared above so they pass through to the sandbox).

## Safety (read first)

- The helper creates a **draft** (`publication_status=4`) by default — nothing goes live.
- A real post uses `publication_status=5` and the helper **refuses it without `--confirm-publish`**.
- Never queue a live post (`--status 5 --confirm-publish`) unless the user has clearly said to publish
  now / to this account. When unsure, create a draft and tell the user it is saved as a draft.

## Connected accounts (project 349678)

Get them live with `accounts`; current ids:

| id | network | name |
|----|---------|------|
| 2180753 | vk | LOVA ceramics |
| 2180775 | instagram | lova_ceramics |
| 2180777 | telegram | LOVA ceramics. С любовью. |
| 2180770 | pinterest | Products |
| 2180767 | pinterest | Авторские лампы и торшеры |
| 2180768 | pinterest | Вазы и кашпо напольные |
| 2180769 | pinterest | Мастерская авторской керамики LOVA CERAMICS |

Pinterest posts usually need a `--title` (and often a `--link`).

## Workflow

1. Confirm the content. There must be post text and image(s) (usually a saved `content_drafts` row).
   Completion: text + image URLs/paths are known.

2. Confirm WHERE and WHEN (usually already chosen in content-studio).
   - The draft normally carries `platforms` and `scheduled_at` because content-studio asks «куда/когда»
     up front. Reuse them; only ask if they are missing or the user changes their mind.
   - `platforms` should hold concrete PostMyPost **account ids** as objects
     (`{"network":"pinterest","account_id":2180768,"name":"Вазы и кашпо напольные"}`), not bare names.
     If a draft still has plain names, resolve them via `accounts` and update the draft's `platforms`
     with the ids — stage 6 cron posts with NO LLM and reads `account_id` straight from the row.
   - WHERE: which accounts (show the list; accept names or networks, map to ids via `accounts`).
     If a network has several connected accounts, resolve to the specific one — Pinterest has 4 boards,
     so «Pinterest» alone is ambiguous; confirm the exact board/`account_id` before publishing.
   - WHEN: «сейчас» → publish now; a time → schedule. Times are always **МСК / Moscow (`+03:00`)** —
     pass `--post-at` as ISO with the offset, e.g. `2026-07-02T10:00:00+03:00`. The helper also
     defaults to `+03:00` when you omit `--post-at`.
   Completion: target `account_ids` and `post_at` (or "now") are known.

3. Publish (or draft).
   - Attach images with `--image-url` (the helper uploads each and gets a `file_id`) or `--file-id`.
   - **Image order = the order of `--image-url` = the order they appear in the post.** The helper
     uploads sequentially and keeps that order in `file_ids`; PostMyPost shows images in `file_ids`
     order. So pass the **product card first** — take the draft's `images` in array order
     (`images[0]` is `role:"card"`, `images[1]` is `role:"raw"`). Never let the card land second.
   - Default is a draft. Publish live only on explicit user confirmation, adding `--status 5 --confirm-publish`.
   - Scheduled: keep the future `--post-at`; for autoposting by cron use draft/scheduled + stage 6.
   Completion: helper returns a publication `id` and `publication_status`.

4. Record the outcome in the content draft.
   - **Update the existing `content_drafts` row in place — never insert a new one.** Use the
     content-studio helper's `update-draft` command (it does an `UPDATE ... WHERE id=`), do not
     re-run `save-draft` and do not hand-write raw psql. This keeps one row per post.
   - Set `status` (`scheduled`/`published`/`failed`), `scheduled_at` (the МСК time; also set it for a
     scheduled post so stage-6 cron sees it), and record the publication id/links in `publication_links`
     with `--append-links` (so several networks accumulate rather than overwrite).

   ```bash
   python <content-studio>/scripts/content_studio.py update-draft --id 16 \
     --status published --scheduled-at "2026-07-02T10:00:00+03:00" \
     --publication-links '[{"id":30811163,"network":"instagram","account_id":2180775}]' --append-links
   ```

   On failure: `update-draft --id <id> --status failed --notes "<why it failed>"`.
   Completion: the same draft row reflects what happened.

## Helper Commands

List accounts:

```bash
python scripts/postmypost.py accounts
```

Upload one image (returns file_id):

```bash
python scripts/postmypost.py upload --url "https://.../photo.jpg"
```

Create a DRAFT (safe default) with an image and text:

```bash
python scripts/postmypost.py publish --account-id 2180777 \
  --content "Текст поста" --title "Заголовок" \
  --image-url "https://.../card.png" --image-url "https://.../photo2.jpg"
```

Schedule a draft for a future time:

```bash
python scripts/postmypost.py publish --account-id 2180777 --content "…" \
  --image-url "https://.../card.png" --post-at "2026-07-02T10:00:00+03:00"
```

Publish LIVE (only on explicit user go):

```bash
python scripts/postmypost.py publish --account-id 2180777 --content "…" \
  --image-url "https://.../card.png" --status 5 --confirm-publish
```

Delete / cancel a publication (also removes drafts):

```bash
python scripts/postmypost.py delete --publication-id 30808023 --account-id 2180777
```

## API notes

- Base URL `https://api.postmypost.io/v4.1`, `Authorization: Bearer <token>`.
- 3-step posting: `/upload/init` (by URL) → `/upload/status` (status 1 = ready, gives `file_id`) →
  `POST /publications`.
- `publication_status`: 4 = draft, 5 = pending publication (live), 10 = template, 11 = workflow, 12 = approval.
- `publication_type`: 1 = post, 2 = story, 4 = reels.
- Delete needs `delete_option` + `account_ids` as flat query params.

## Common Pitfalls

1. Publishing live by accident.
   Fix: default to a draft; add `--status 5 --confirm-publish` only on explicit user confirmation.

2. Posting to Pinterest without a title.
   Fix: give `--title` (and usually `--link`) for pinterest accounts.

2a. Guessing which account when a network has several.
   Fix: Pinterest has 4 boards — ask the user which one (or suggest the board matching the product)
   and publish to that exact `account_id`, never to all Pinterest boards by default.

2b. Card ends up second in the post.
   Fix: image order follows the order of `--image-url` (the helper preserves it in `file_ids`). Pass the
   card first — iterate the draft's `images` in array order, where `images[0]` is the `role:"card"` card.

3. Losing the outcome.
   Fix: after publishing, update the `content_drafts` row (status, scheduled_at, publication_links).

## Verification Checklist

- [ ] `accounts` lists the connected networks with ids.
- [ ] `upload --url` returns a `file_id`.
- [ ] `publish` (default) creates a draft (`publication_status: 4`, `is_draft: true`).
- [ ] `delete` removes a publication (draft count returns to previous).
- [ ] Live publish only happens with `--confirm-publish` after explicit user go.
