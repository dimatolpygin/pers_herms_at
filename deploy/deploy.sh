#!/usr/bin/env bash
# Серверный редеплой Hermes. Запускается вручную и из CI (GitHub Actions) при push в master.
# Шаги: git pull → поднять Postgres → миграции → sync навыков → рестарт gateway.
# hermes-agent установлен НАТИВНО; здесь только надстройка (навыки/миграции/cron).
set -euo pipefail

INSTALL_DIR="${INSTALL_DIR:-/opt/pers_herms_at}"
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
SKILLS_SRC="$INSTALL_DIR/hermes-skills"
SKILLS_DST="$HERMES_HOME/skills"

cd "$INSTALL_DIR"

echo "== git =="
git fetch --all -q
git reset --hard origin/master -q
echo "repo @ $(git rev-parse --short HEAD)"

echo "== postgres =="
# deploy/.env (вне git) хранит пароль БД. Генерим один раз при первом деплое.
if [ ! -f "$INSTALL_DIR/deploy/.env" ]; then
  {
    echo "POSTGRES_USER=postgres"
    echo "POSTGRES_DB=mydb"
    echo "POSTGRES_PASSWORD=$(openssl rand -hex 24)"
  } > "$INSTALL_DIR/deploy/.env"
  chmod 600 "$INSTALL_DIR/deploy/.env"
  echo "deploy/.env создан (сгенерирован POSTGRES_PASSWORD)"
fi
docker compose --env-file "$INSTALL_DIR/deploy/.env" -f "$INSTALL_DIR/deploy/docker-compose.yml" up -d

echo "== ждём готовности БД =="
for _ in $(seq 1 30); do
  if docker exec hermes-postgres pg_isready -U postgres -d mydb >/dev/null 2>&1; then break; fi
  sleep 2
done

echo "== migrations =="
bash "$INSTALL_DIR/deploy/migrate.sh"

echo "== sync навыков =="
# Наши навыки → рабочая директория hermes. repo = источник правды.
# (Живые self-edit'ы агента через skill_manage перезапишутся репозиторием при деплое —
#  это ожидаемо: деплой редкий, git главнее.)
mkdir -p "$SKILLS_DST"
rsync -a "$SKILLS_SRC/" "$SKILLS_DST/"
echo "навыки → $SKILLS_DST"

echo "== sync хуков =="
# Хук профильного сторожа (hooks/profile_guard.*) — тоже из репо. Без этого он
# живёт только на проде и теряется при переезде или самообновлении hermes.
# Сам блок hooks: в config.yaml правится один раз руками, конфиг не в git.
mkdir -p "$HERMES_HOME/hooks"
rsync -a "$INSTALL_DIR/hooks/" "$HERMES_HOME/hooks/"
chmod 755 "$HERMES_HOME/hooks"/*.py 2>/dev/null || true
echo "хуки → $HERMES_HOME/hooks"

echo "== профили =="
# Рестарт gateway в конце деплоя УБЬЁТ работающего воркера kanban (SIGKILL по
# cgroup, замерено 28.08.2026: задача дважды «crashed / pid not alive» и была
# брошена). Поэтому деплой при занятой доске останавливается — работа клиента
# дороже свежести кода на пару минут.
if hermes kanban list --status running 2>/dev/null | grep -qE '^\s*[●▶]'; then
  echo "На доске есть работающие задачи — деплой отложен, чтобы не убить воркеров:"
  hermes kanban list --status running 2>/dev/null | sed 's/^/   /'
  echo "Повторить после их завершения (или DEPLOY_IGNORE_RUNNING=1, если уверен)."
  [ "${DEPLOY_IGNORE_RUNNING:-0}" = "1" ] || exit 1
fi

# Профили — дистрибутивы (см. docs/10_PROFILE_CONVEYOR.md). Перекатываем ТОЛЬКО
# те, что уже стоят как дистрибутив: у них есть distribution.yaml, то есть их
# ставили конвейером и источник правды у них — этот репозиторий. Профиль,
# заведённый мимо конвейера, деплой не трогает — иначе один git push молча
# переписал бы клиенту то, что он настраивал руками.
# Новый профиль ставится явно: python3 scripts/profile_forge.py install <имя>
for pdir in "$HERMES_HOME"/profiles/*/; do
  pname="$(basename "$pdir")"
  [ -f "$pdir/distribution.yaml" ] || continue
  [ -d "$INSTALL_DIR/profiles/$pname" ] || continue
  echo "-- $pname"
  python3 "$INSTALL_DIR/scripts/profile_forge.py" install "$pname" 2>&1 | sed 's/^/   /'
done

# Карта зон сторожа генерируется форджем в клон репозитория, а rsync хуков
# прошёл ВЫШЕ — без этой строки на прод уехала бы версия из коммита, а не
# пересобранная. Пока они совпадают, но разойдутся в первый же раз, когда
# реестр правят и забывают выполнить `profile_forge.py guard` перед пушем.
cp "$INSTALL_DIR/hooks/profile_guard.json" "$HERMES_HOME/hooks/profile_guard.json"

echo "== сторож =="
# Файл хука пересинхронизирован, его mtime изменился — hermes честно скажет
# «script modified since approval». Это предупреждение, а не отказ: хук
# остаётся в allowlist и работает. Печатаем проверку, чтобы падение сторожа
# было видно в логе деплоя, а не обнаруживалось по нулевому делегированию.
hermes hooks doctor 2>&1 | sed 's/^/   /' || true

echo "== рестарт gateway =="
# reload, а не restart: у юнита ExecReload=kill -USR1, а SIGUSR1 у gateway —
# штатный in-band рестарт (отказаться от новой работы, доработать текущие ходы,
# выйти с кодом 75, systemd поднимет заново). restart рубит ходы на полуслове.
systemctl reload hermes-gateway || systemctl restart hermes-gateway
sleep 5
if systemctl is-active --quiet hermes-gateway; then
  echo "gateway: active"
else
  echo "gateway НЕ поднялся, последние логи:"
  journalctl -u hermes-gateway -n 40 --no-pager || true
  exit 1
fi

echo "deploy: готово"
