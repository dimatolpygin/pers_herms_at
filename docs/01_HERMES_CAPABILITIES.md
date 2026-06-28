# Карта возможностей Hermes Agent (что уже есть в репо)

Анализ `github.com/NousResearch/hermes-agent` (HEAD `2ecb6f7`, изучено 2026-06-28 через GitHub API).
Главный вывод: **значительная часть наших этапов покрыта из коробки**. Hermes — Python-фреймворк
агентной оболочки с плагинами, навыками (skills), мульти-платформенным gateway, памятью, cron.

> ⚠️ `env_пример.txt` в корне проекта (Telegraf / `BOT_TOKEN` / `PI_MODEL` / Whisper) — это конфиг
> ДРУГОЙ, Node-обвязки, НЕ этого Python-репо. Реальные переменные Hermes — в его `.env.example`
> и `~/.hermes/config.yaml`. Не путать.

## Что встроено и на какой наш этап ложится

| Возможность Hermes | Где в репо | Наш этап |
|---|---|---|
| **Telegram-платформа** (+ Discord/Slack/WhatsApp/Signal/Email…) | `plugins/platforms/telegram` | 0, 1 |
| **Голосовые → транскрипция** (voice memo transcription) | gateway media pipeline | 1 |
| **Долгосрочная память / модель пользователя** (mem0, honcho, supermemory, retaindb, holographic, hindsight…) | `plugins/memory/*` | 2 |
| **Self-improving loop**: сам создаёт/улучшает skills, помнит между сессиями | ядро + skills | 1, 2 |
| **Роутинг моделей через OpenRouter** (и Novita/Gemini/GLM/Kimi…) | `.env` + `~/.hermes/config.yaml` | 0, 1 |
| **Генерация картинок** (fal, openai, openrouter, xai, krea) — **kie.ai НЕТ** | `plugins/image_gen/*` | 4 (добавим kie.ai) |
| **Cron-планировщик** с доставкой в платформу | `plugins/cron_providers`, `cron/`, skill cron | 6 |
| **Навык social-media** (заготовка) | `skills/social-media` | 4, 5 |
| **Skills-система** (процедурная память, Skills Hub) | `skills/`, `optional-skills/` | 3, 4, 5 |
| **Shell/file-инструменты, browser, computer-use** | `tools/`, `plugins/browser` | 3, 4 |
| **Безопасность**: approval команд, DM pairing, allowlist user | gateway authz | 0 |
| **Docker / docker-compose** (в т.ч. windows) | `Dockerfile`, `docker-compose*.yml` | 7 |

## Что придётся делать кастомно (наш код / навыки)

1. **kie.ai (gpt-image-2) как провайдер картинок** — в `plugins/image_gen/` нет kie.ai. Добавить провайдер или навык-обёртку (createTask → polling recordInfo → resultUrls). Этап 4.
2. **Навык docx (КП) + csv (отчёты)** — генерация деловых документов и отправка файлом. Этап 3.
3. **Навык «контент»** — гибкий сценарий: вход свободный/ссылка/Tilda → 3 текста + картинка → превью. Этап 4.
4. **Навык публикации PostMyPost** — 3-шаговая интеграция (загрузка фото → проверка → публикация), выбор площадок/времени. Этап 5.
5. **Таблица контента (Postgres) + cron-обвязка постинга** без LLM. Этапы 4, 6.
6. **STT без OpenAI** — проверить, какой провайдер транскрипции Hermes использует и заменить на доступный через OpenRouter / SYNTX.AI (блокер этапа 1).

## Установка (нативная Windows)

- Инсталлятор: `iex (irm https://hermes-agent.nousresearch.com/install.ps1)`
  Ставит изолированно в `%LOCALAPPDATA%\hermes`: uv, Python 3.11, Node.js, ripgrep, ffmpeg, portable Git Bash. Без админ-прав.
- Настройка: `hermes setup` (мастер), модель — `hermes model`.
- Запуск: `hermes` (TUI) или `hermes gateway` (Telegram/Discord/…).
- Конфиг: `~/.hermes/config.yaml` (модель по умолчанию и пр.) + `.env` (ключи).
- Возможный нюанс: антивирус может ложно флагать `uv.exe` в `%LOCALAPPDATA%\hermes\bin`.

## Ключевые точки конфигурации для нас

- `OPENROUTER_API_KEY` — ключ клиента из `доступы.txt`.
- Telegram bot token `@Mironov_AgentBot` + allowlist `ALLOWED_USER_IDS`/DM pairing → только Mika (id `5183294551`).
- Модель по умолчанию — DeepSeek V4 Pro через OpenRouter (`hermes model`).
