---
name: s3-upload
description: Используй, когда пользователь присылает СВОЁ фото/картинку и хочет получить на неё публичную ссылку, или хочет использовать своё фото в посте — триггеры «загрузи это фото», «залей картинку в облако», «дай ссылку на это фото», «используй моё фото для поста», «вот моё фото, запости его», «сохрани картинку и дай ссылку». Загружает локальный файл или картинку по URL в S3-бакет клиента (Beget) и возвращает стабильную публичную ссылку. Эту ссылку затем можно переиспользовать как картинку role="raw" в посте (content-studio / postmypost).
version: 1.0.0
author: Hermes Agent Project
license: MIT
required_environment_variables:
  - name: S3_ENDPOINT
    optional: true
    prompt: S3 endpoint, e.g. https://s3.ru1.storage.beget.cloud
  - name: S3_REGION
    optional: true
    prompt: S3 region, e.g. ru-central-1
  - name: S3_BUCKET
    optional: true
    prompt: S3 bucket name
  - name: S3_ACCESS_KEY
    optional: true
    prompt: S3 access key id
  - name: S3_SECRET_KEY
    optional: true
    prompt: S3 secret access key
  - name: S3_PUBLIC_BASE_URL
    optional: true
    prompt: Public base URL for links (default = S3_ENDPOINT)
metadata:
  hermes:
    tags: [media, storage, s3, upload, images]
    related_skills: [content-studio, postmypost-publish]
---

# S3 Upload

## Overview

Uploads an image to the client's S3-compatible bucket and returns a **public URL**.
Main use: the user sends their own photo and wants to use it (e.g. in a social post),
so it needs a stable public link instead of a temporary Telegram/CDN URL.

Use the bundled `scripts/s3_upload.py` helper. Credentials come from the `S3_*`
environment variables (declared above so they pass through to the sandbox).
Dependency-light — stdlib only, AWS Signature v4, no boto3.

## When to use

- The user sends a photo/image and asks to upload it, get a link, or use it in a post.
- You have an image URL (incl. the incoming Telegram file URL) or a local file path and
  need a durable public link for it.
- Before publishing: to turn a user's own photo into a `role:"raw"` image for the post.

Do NOT use this for images the agent generated itself via `content-studio` (those are
already stored) — this skill is for the user's OWN photos.

## Workflow

1. Get the source. Either a local file path (a downloaded photo) or an image URL
   (a Telegram file URL works). Prefer whatever the platform already gives you.
2. Upload it:

   ```bash
   # from a local file
   python scripts/s3_upload.py upload --file "/path/to/photo.jpg"

   # from a remote URL (downloads, then uploads)
   python scripts/s3_upload.py upload --url "https://.../photo.jpg"
   ```

   The helper returns JSON: `{"success": true, "url": "...", "key": "...", ...}`.
   Give the user that `url`.
3. (Optional) Use it in a post. The returned URL is a normal public image URL, so pass
   it as an extra `--image-url` to the `postmypost` helper, or add it to the draft's
   `images` array as `{"role":"raw","url":"<the-url>"}` via content-studio. It appears in
   the post in the usual image order (card first, then the user's photo).

## Helper Commands

Upload a local file (key auto-generated as `user-uploads/<date>_<uuid>.<ext>`):

```bash
python scripts/s3_upload.py upload --file "photo.jpg"
```

Upload from a URL, with a custom key prefix:

```bash
python scripts/s3_upload.py upload --url "https://.../photo.jpg" --prefix "user-uploads"
```

Set an explicit key or override content type:

```bash
python scripts/s3_upload.py upload --file "photo.png" --key "user-uploads/my-name.png" \
  --content-type "image/png"
```

If the bucket is already public via bucket policy and rejects per-object ACLs, disable the
ACL header:

```bash
python scripts/s3_upload.py upload --file "photo.jpg" --no-acl
```

## Notes

- Public URL is path-style: `{S3_PUBLIC_BASE_URL}/{S3_BUCKET}/{key}`.
- Objects are uploaded with `x-amz-acl: public-read` by default so the link is readable.
- Keys default to `user-uploads/<UTC-date>_<uuid8>.<ext>` — no collisions, easy to spot.

## Verification Checklist

- [ ] `upload --file` returns `success: true` with a `url`.
- [ ] `upload --url` (remote source) returns `success: true` with a `url`.
- [ ] The returned URL opens in a browser and shows the image (HTTP 200, right content-type).
- [ ] The URL can be passed to `postmypost` as `--image-url` / added to a draft as `role:"raw"`.
