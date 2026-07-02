#!/usr/bin/env bash
# Накат migrations/*.sql по порядку в Postgres-контейнер с учётом уже применённых.
# Идемпотентно: каждый файл применяется один раз (учёт в public.schema_migrations).
set -euo pipefail

CONTAINER="${PG_CONTAINER:-hermes-postgres}"
DB="${POSTGRES_DB:-mydb}"
DB_USER="${POSTGRES_USER:-postgres}"
MIG_DIR="$(cd "$(dirname "$0")/../migrations" && pwd)"

psql() { docker exec -i "$CONTAINER" psql -U "$DB_USER" -d "$DB" -v ON_ERROR_STOP=1 "$@"; }

psql -c "CREATE TABLE IF NOT EXISTS public.schema_migrations (filename text PRIMARY KEY, applied_at timestamptz DEFAULT now());" >/dev/null

for f in "$MIG_DIR"/*.sql; do
  name="$(basename "$f")"
  applied="$(psql -tAc "SELECT 1 FROM public.schema_migrations WHERE filename = '$name';")"
  if [ "$applied" = "1" ]; then
    echo "skip  $name (уже применена)"
    continue
  fi
  echo "apply $name"
  psql < "$f"
  psql -c "INSERT INTO public.schema_migrations(filename) VALUES ('$name');" >/dev/null
done

echo "migrations: готово"
