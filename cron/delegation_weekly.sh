#!/usr/bin/env bash
# Еженедельный замер делегирования. Инвариант 6 блока v8: «стало лучше или хуже»
# должно оставаться проверяемым и после сдачи, а не быть разовой цифрой на приёмке.
#
# Пишет одну строку в /root/hermes-metrics/delegation_log.md. Файл лежит ВНЕ клона
# репозитория намеренно: deploy.sh делает `git reset --hard`, всё несохранённое
# внутри /opt/pers_herms_at он бы стирал.
#
# Забрать журнал в репозиторий:
#   pscp root@89.125.17.86:/root/hermes-metrics/delegation_log.md docs/metrics/
set -euo pipefail

METRICS_DIR="${METRICS_DIR:-/root/hermes-metrics}"
SCRIPT="${SCRIPT:-/opt/pers_herms_at/scripts/delegation_metric.py}"
LOG="$METRICS_DIR/delegation_log.md"
REVIEW="$METRICS_DIR/review.json"
STAMP="$(date +%Y-%m-%d)"

mkdir -p "$METRICS_DIR"

if [ ! -f "$LOG" ]; then
  {
    echo "# Журнал делегирования (еженедельно, cron)"
    echo
    echo "Снимает \`scripts/delegation_metric.py\` — тот же скрипт, что дал baseline,"
    echo "иначе цифры несравнимы. Порядок замера — \`docs/metrics/README.md\`."
    echo
    echo "\`Задач\` — только те, где работа реально делалась: разговоры без работы"
    echo "в знаменатель не входят, там нечего было делегировать."
    echo
    echo "| Дата | Окно | Задач | Отдано профилю | Доля |"
    echo "|---|---|---:|---:|---:|"
  } > "$LOG"
fi

OUT="$METRICS_DIR/weekly_$STAMP"
SINCE="$(date -d '7 days ago' +%Y-%m-%d)"

ARGS=(--domain docs --since "$SINCE" --out "$OUT.md" --json "$OUT.json")
# Ручные вердикты подмешиваем, только если файл есть: без него автоотбор
# считает уточняющий ход («ушёл к Мике за реквизитами») как «сделал сам».
# `|| true` обязателен: при set -e голое `[ ] && ...` с ложным условием роняет скрипт.
[ -f "$REVIEW" ] && ARGS+=(--review "$REVIEW") || true

python3 "$SCRIPT" "${ARGS[@]}"

python3 - "$OUT.json" "$LOG" "$STAMP" "$SINCE" <<'PY'
import json, sys
data = json.load(open(sys.argv[1], encoding='utf-8'))
log, stamp, since = sys.argv[2], sys.argv[3], sys.argv[4]
worked = data.get('worked') or 0
deleg = data.get('delegated') or 0
share = data.get('share_pct')
share = '—' if not worked else ('%.0f %%' % share if share is not None else '—')
row = '| %s | c %s | %d | %d | %s |\n' % (stamp, since, worked, deleg, share)
with open(log, 'a', encoding='utf-8') as f:
    f.write(row)
print(row.strip())
PY
