---
name: postmypost-publish
description: Use when the user wants to publish or schedule a prepared post to social networks — Russian triggers like «запости», «опубликуй», «выложи в соцсети», «постни», «запланируй пост», «отправь в инсту/телеграм/вк/пинтерест». Takes a content draft (text + image/card from content-studio), asks WHERE (which of the connected accounts) and WHEN (now or a scheduled time), then publishes through PostMyPost. Creates a DRAFT by default; a live post requires explicit user confirmation.
version: 1.0.0
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

2. Ask WHERE and WHEN.
   - WHERE: which accounts (show the list; accept names or networks, map to ids via `accounts`).
   - WHEN: «сейчас» → publish now; a time → schedule (`--post-at` ISO, e.g. `2026-07-02T10:00:00+03:00`).
   Completion: target `account_ids` and `post_at` (or "now") are known.

3. Publish (or draft).
   - Attach images with `--image-url` (the helper uploads each and gets a `file_id`) or `--file-id`.
   - Default is a draft. Publish live only on explicit user confirmation, adding `--status 5 --confirm-publish`.
   - Scheduled: keep the future `--post-at`; for autoposting by cron use draft/scheduled + stage 6.
   Completion: helper returns a publication `id` and `publication_status`.

4. Record the outcome in the content draft.
   - Update the `content_drafts` row: `status` (`scheduled`/`published`/`failed`), `scheduled_at`,
     and the publication id/links in `publication_links`.
   Completion: the draft reflects what happened.

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

3. Losing the outcome.
   Fix: after publishing, update the `content_drafts` row (status, scheduled_at, publication_links).

## Verification Checklist

- [ ] `accounts` lists the connected networks with ids.
- [ ] `upload --url` returns a `file_id`.
- [ ] `publish` (default) creates a draft (`publication_status: 4`, `is_draft: true`).
- [ ] `delete` removes a publication (draft count returns to previous).
- [ ] Live publish only happens with `--confirm-publish` after explicit user go.
