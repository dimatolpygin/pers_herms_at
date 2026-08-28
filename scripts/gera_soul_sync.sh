#!/usr/bin/env bash
# Синхронизация SOUL.md Геры между продом и репозиторием.
#
# Почему это отдельный скрипт, а не конвейер профилей.
# Профили (`profiles/<имя>/`) живут дистрибутивами: источник правды — git,
# `hermes profile update` перекатывает SOUL с гита на прод. С Герой так НЕЛЬЗЯ.
# Его SOUL правит сам клиент — и правит через агента: 27.08.2026 Мика попросил
# Геру исправиться, и тот дописал себе стоп-правило про .docx командой `patch`
# прямо из телеграм-сессии. Раскатать git поверх — значит молча стереть работу,
# которую клиент считает своей.
#
# Поэтому направление по умолчанию обратное: ПРОД -> РЕПО.
# Git здесь не хозяин файла, а его история: чтобы правки клиента были видны
# диффом, а падение сервера не уносило 37 КБ настроенного поведения.
# (До этого локальная копия в .secrets/ была от 02.07 — 15 088 Б против 37 106
#  на проде, и расхождение никто не замечал.)
#
# Команды:
#   bash scripts/gera_soul_sync.sh pull   — забрать с прода в репозиторий
#   bash scripts/gera_soul_sync.sh diff   — показать расхождение, ничего не менять
#   bash scripts/gera_soul_sync.sh push   — залить репо на прод. ОПАСНО, спросит
#                                           подтверждение и сделает бэкап на проде.
set -euo pipefail

REPO_FILE="$(cd "$(dirname "$0")/.." && pwd)/profiles/default/SOUL.md"
REMOTE="${GERA_HOST:-root@89.125.17.86}"
REMOTE_FILE="/root/.hermes/SOUL.md"
CMD="${1:-diff}"

# Windows-хост ходит через plink/pscp, Linux — через ssh/scp.
if command -v pscp >/dev/null 2>&1 || [ -x "/c/Program Files/PuTTY/pscp.exe" ]; then
  PSCP="${PSCP:-/c/Program Files/PuTTY/pscp.exe}"
  PLINK="${PLINK:-/c/Program Files/PuTTY/plink.exe}"
  HOSTKEY="${GERA_HOSTKEY:?задай GERA_HOSTKEY}"
  PW="${GERA_PW:?задай GERA_PW}"
  copy_from() { "$PSCP" -hostkey "$HOSTKEY" -pw "$PW" "$REMOTE:$REMOTE_FILE" "$1"; }
  copy_to()   { "$PSCP" -hostkey "$HOSTKEY" -pw "$PW" "$1" "$REMOTE:$REMOTE_FILE"; }
  remote()    { "$PLINK" -batch -hostkey "$HOSTKEY" -pw "$PW" "$REMOTE" "$1"; }
else
  copy_from() { scp "$REMOTE:$REMOTE_FILE" "$1"; }
  copy_to()   { scp "$1" "$REMOTE:$REMOTE_FILE"; }
  remote()    { ssh "$REMOTE" "$1"; }
fi

TMP="$(mktemp)"
trap 'rm -f "$TMP"' EXIT

case "$CMD" in
  pull)
    copy_from "$TMP"
    if [ -f "$REPO_FILE" ] && cmp -s "$TMP" "$REPO_FILE"; then
      echo "совпадает, менять нечего ($(wc -c < "$REPO_FILE") Б)"
      exit 0
    fi
    mkdir -p "$(dirname "$REPO_FILE")"
    cp "$TMP" "$REPO_FILE"
    echo "забрано с прода: $(wc -c < "$REPO_FILE") Б -> $REPO_FILE"
    echo "коммить отдельно: git commit -m 'docs(soul): правки Геры от <дата>'"
    ;;

  diff)
    copy_from "$TMP"
    if [ ! -f "$REPO_FILE" ]; then
      echo "в репозитории копии нет — сделай pull"
      exit 1
    fi
    if cmp -s "$TMP" "$REPO_FILE"; then
      echo "совпадает ($(wc -c < "$REPO_FILE") Б)"
    else
      echo "РАСХОЖДЕНИЕ: прод $(wc -c < "$TMP") Б, репо $(wc -c < "$REPO_FILE") Б"
      diff -u "$REPO_FILE" "$TMP" || true
    fi
    ;;

  push)
    # Направление против шерсти: перезаписывает то, что мог написать клиент.
    echo "Это зальёт $REPO_FILE поверх $REMOTE_FILE и сотрёт правки, сделанные"
    echo "клиентом через агента. Набери YES, чтобы продолжить:"
    read -r answer
    [ "$answer" = "YES" ] || { echo "отменено"; exit 1; }
    stamp="$(date +%Y-%m-%d-%H%M)"
    remote "cp $REMOTE_FILE $REMOTE_FILE.bak-$stamp && ls -la $REMOTE_FILE.bak-$stamp"
    copy_to "$REPO_FILE"
    echo "залито. Бэкап на проде: $REMOTE_FILE.bak-$stamp"
    echo "Гера перечитывает SOUL при старте сессии — рестарт не нужен."
    ;;

  *)
    echo "использование: $0 {pull|diff|push}" >&2
    exit 2
    ;;
esac
