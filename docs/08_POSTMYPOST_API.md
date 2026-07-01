# PostMyPost API — рабочий референс (этап 5)

Чистый конспект без секретов. Боевые доступы (project_id + Bearer-токен + список аккаунтов)
лежат в `postmypost.txt` (вне git) и в `%LOCALAPPDATA%\hermes\.env`
(`POSTMYPOST_TOKEN`, `POSTMYPOST_PROJECT_ID`). Официальная дока: https://help.postmypost.io/ru/docs/api/

## Базовое

- Base URL: `https://api.postmypost.io/v4.1`
- Авторизация: заголовок `Authorization: Bearer <token>`
- Ответы — JSON-массивы (`[{...}]`), у ошибок поле `message`/`status`
- Rate-limit заголовки: `X-Rate-Limit-Limit`, `-Remaining`, `-Reset`, `Retry-After`

## Публикация идёт в 3 шага

### 1. Загрузка фото по URL
`POST /upload/init` — тело `{"project_id": <id>, "url": "https://.../photo.jpg"}`
→ `[{"id": 13976, "url": "...", "size": ..., "status": 5}]`

### 2. Проверка статуса загрузки
`GET /upload/status?id=<id>` → `[{"id": 13976, "file_id": 50691633, "status": 1}]`
Статусы: `1` — успех (есть `file_id`), `2` — ошибка. Берём `file_id` для публикации.

### 3. Создание публикации
`POST /publications` — тело:

```json
{
  "project_id": 349678,
  "post_at": "2026-07-02T10:00:00+03:00",
  "account_ids": [2180777],
  "publication_status": 4,
  "details": [
    { "publication_type": 1, "file_ids": [50691633], "content": "Текст поста", "title": "Заголовок" }
  ]
}
```

Ответ: `[{ "id": ..., "publication_status": 4, "post_at": ..., "account_ids": [...] }]`

## Энумы

- `publication_status` (для создания): **4 — черновик**, **5 — в очередь на публикацию (live)**,
  10 — шаблон, 11 — этап воркфлоу, 12 — согласование.
  (в ответах ещё: 0 — удалён, 1 — опубликован, 2 — публикуется, 3 — ошибка, 6 — не удалён из-за ошибки)
- `publication_type`: 1 — пост, 2 — story, 4 — reels/shorts/clips
- `chanel_id` (у аккаунтов): 1 — Instagram, 2 — ВК, 6 — Telegram, 7 — Pinterest

## Аккаунты

`GET /accounts?project_id=<id>` → список `{id, chanel_id, external_id, name, login, connection_status}`.
Публиковать нужно по `id` аккаунта (не `external_id`).

## Удаление / отмена

`DELETE /publications/{id}` — обязательные **flat query-параметры**: `delete_option` (1 — убрать из
PostMyPost) и `account_ids` (по одному на каждый аккаунт), плюс `project_id`. Убирает и черновики.
Примечание: PMP может вернуть 422 «Response validation error … publication_status», но удаление при
этом проходит (проверять по списку).

## Безопасность в проекте

- Helper `hermes-skills/social-publishing/postmypost/scripts/postmypost.py` по умолчанию создаёт
  **черновик** (status 4). Живая публикация (status 5) — только с флагом `--confirm-publish`.
- Токен только в `.env`/`postmypost.txt` (оба вне git), в код и доки не попадает.
