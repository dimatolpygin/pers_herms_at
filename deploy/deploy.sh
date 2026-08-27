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

echo "== рестарт gateway =="
systemctl restart hermes-gateway
sleep 3
if systemctl is-active --quiet hermes-gateway; then
  echo "gateway: active"
else
  echo "gateway НЕ поднялся, последние логи:"
  journalctl -u hermes-gateway -n 40 --no-pager || true
  exit 1
fi

echo "deploy: готово"
