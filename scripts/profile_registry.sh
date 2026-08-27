#!/bin/bash
# Реестр маршрутизации профилей — единственный источник правды о том, кому что отдавать.
#
# Инвариант 5 блока v8: маршрутизация берётся из машинного реестра, а не из прозы в SOUL.
# Здесь задаются описания профилей (`hermes profile describe`), и отсюда же
# генерируется таблица ROUTING.md для навыка Геры `meta/profile-delegate`.
#
# Добавить профиль в конвейер = добавить один блок describe ниже и выполнить скрипт.
# Ни SOUL Геры, ни навык руками править не надо.
#
# Запуск на сервере:  bash /root/.hermes/scripts/profile_registry.sh
set -euo pipefail

PROFILES="content dev docs seo strategist writer"
ROUTING="/root/.hermes/skills/meta/profile-delegate/ROUTING.md"

# ─────────────────────────── описания ───────────────────────────
# Формат описания, важный для маршрутизации: что сюда идёт + чего сюда НЕ надо.
# Второе не менее важно первого: с восемью профилями зоны начнут пересекаться,
# и «не сюда» — единственное, что удерживает задачу от ухода не туда.

hermes profile describe docs --overwrite --text \
'Документы ИП Миронов М.Ю. (LOVA CERAMICS): коммерческие предложения, счета-оферты, закрывающие документы и акты, расчёт налогов УСН 6% без НДС. Собирает .docx и .xlsx по эталонным образцам — с реквизитами, логотипом и проверкой арифметики критиком. Сюда идёт всё, где результат — файл документа с цифрами и реквизитами. Не сюда: посты в соцсети, тексты для сайта, код.'

hermes profile describe content --overwrite --text \
'Соцсети LOVA CERAMICS: посты для Instagram, Telegram, VK, Pinterest, генерация и доработка картинок (kie.ai, FAL.ai), автопостинг через PostMyPost, описания товаров и категорий, загрузка фото в S3. Не сюда: мета-теги и прочая SEO-механика — это seo; литературная редактура — это writer.'

hermes profile describe seo --overwrite --text \
'SEO-механика на Tilda, только исполнение: массовая замена title, description и H1, прописывание alt у картинок, снятие частотностей по готовому списку ключей, проверка индексации, robots.txt и sitemap. Не сюда: семантическое ядро, кластеризация и приоритеты — это strategist.'

hermes profile describe strategist --overwrite --text \
'Стратегия LOVA CERAMICS: семантическое ядро и кластеризация, маркетинговые и SEO-стратегии, позиционирование бренда, приоритеты, разбор рынка и конкурентов. Сюда идут решения о том, ЧТО делать. Исполнение решения уходит дальше в seo, content или docs.'

hermes profile describe dev --overwrite --text \
'Код и автоматизация: скрипты, приложения, расширения Chrome, Playwright, веб-код HTML/CSS/JS, backend на Python, интеграции по API, серверные задачи. Сюда идёт всё, где результат — работающий код.'

hermes profile describe writer --overwrite --text \
'Литературная работа: книга «Семьи» и другие авторские тексты, корректура, вычитка, профессиональная редактура, стилистическая правка, снятие AI-штампов и канцелярита. Не сюда: продающие посты — это content; документы с реквизитами — это docs.'

# ──────────────────── генерация ROUTING.md ────────────────────
# Модель берётся из `hermes profile list`, а не пишется руками: в навыке клиента
# таблица моделей уже разошлась с реальностью (seo значился на Gemini Flash,
# фактически DeepSeek). Сгенерированный файл разойтись не может.

mkdir -p "$(dirname "$ROUTING")"
{
  echo "<!-- СГЕНЕРИРОВАНО scripts/profile_registry.sh — руками не править. -->"
  echo "<!-- Обновить: bash /root/.hermes/scripts/profile_registry.sh -->"
  echo
  echo "# Кому какая задача"
  echo
  echo "Дата сборки: $(date '+%Y-%m-%d %H:%M %Z'). Источник — \`hermes profile describe\`."
  echo
  for p in $PROFILES; do
    model="$(hermes profile list 2>/dev/null | awk -v p="$p" '$1==p {print $2}')"
    desc="$(hermes profile describe "$p" 2>/dev/null | head -1)"
    echo "## \`$p\`"
    echo
    echo "Модель: ${model:-—}"
    echo
    echo "$desc"
    echo
  done
  echo "---"
  echo
  echo "Имя профиля в \`kanban_create(assignee=...)\` пишется ровно так, как в заголовке."
  echo "Карточка с неизвестным assignee молча остаётся в \`ready\` навсегда."
} > "$ROUTING"

echo "--- РЕЕСТР ---"
for p in $PROFILES; do
  printf '%-12s ' "$p"
  hermes profile describe "$p" 2>&1 | head -1 | cut -c1-90
done
echo
echo "ROUTING.md собран: $ROUTING ($(wc -c < "$ROUTING") Б)"
