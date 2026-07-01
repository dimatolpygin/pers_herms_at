# Автопостинг по расписанию (этап 6)

`autopost.py` — cron-обвязка, публикующая запланированные черновики через PostMyPost
**без вызова LLM**. Работает только с БД (`hermes_agent.content_drafts`) и PostMyPost API.

## Как это работает

Один проход (`python cron/autopost.py`) делает:

1. Берёт черновики `status='scheduled'` с наступившим `scheduled_at` (МСК).
2. **Атомарно клеймит** каждый: `UPDATE … status='publishing' WHERE status='scheduled'`.
   Повторный или параллельный прогон этот же ряд уже не подхватит → **пост не уйдёт дважды**.
3. Грузит картинки один раз (карточка первой), создаёт публикацию на каждый
   `platforms[].account_id` (live, `publication_status=5`). Pinterest получает `title`+`link`.
4. Пишет итог обратно: `status` → `published` (или `failed` с `notes`), дозаписывает
   `publication_links`. Через `content_studio.py update-draft` (UPDATE на месте).

Секреты (`POSTMYPOST_TOKEN`, `POSTMYPOST_PROJECT_ID`, `CONTENT_PSQL_COMMAND`) скрипт
автоматически подхватывает из `%LOCALAPPDATA%\hermes\.env` (или `HERMES_ENV_FILE`).

## Запуск

Проверка без публикации:

```bash
python cron/autopost.py --dry-run
```

Боевой проход (публикует всё, что созрело):

```bash
python cron/autopost.py
```

## Расписание

**Локально (Windows)** — Task Scheduler, каждые 1–5 минут:

```powershell
schtasks /Create /TN HermesAutopost /TR "D:\claude\хермес\cron\run-autopost.bat" /SC MINUTE /MO 5
```

**На сервере (этап 7, Linux)** — cron, каждую минуту:

```cron
* * * * * cd /app && python cron/autopost.py >> /app/logs/autopost.log 2>&1
```

Скрипт идемпотентен, так что частый запуск безопасен: он публикует только созревшие
записи и никогда не постит одно и то же дважды.
