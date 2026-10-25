#!/usr/bin/env bash
# Staging-only real stock transactions in a transient separate PostgreSQL.
# Does not connect to the live staging PostgreSQL or change an existing volume.
set -Eeuo pipefail
umask 077
cd "$(dirname "$0")/.."

test -f docker-compose.staging.yml && test -f .env || {
  echo "ISOLATED_STOCK_UAT=BLOCKED missing_staging_files"
  exit 2
}
docker network inspect tgstage_net >/dev/null 2>&1 || {
  echo "ISOLATED_STOCK_UAT=BLOCKED staging_network_missing"
  exit 2
}

container="tgstage_stock_drill_$(date +%s)_$$"
db="office_assistant_stock_acceptance_test"
user="stock_acceptance"
password="synthetic-stock-acceptance-not-a-production-secret"

cleanup() {
  docker rm -f "$container" >/dev/null 2>&1 || true
}
trap cleanup EXIT

# Dedicated ephemeral tmpfs: no host volume, no published port, no production data.
docker run -d --rm --pull=never \
  --name "$container" --network tgstage_net \
  --tmpfs /var/lib/postgresql/data:rw,size=256m \
  -e "POSTGRES_DB=$db" -e "POSTGRES_USER=$user" \
  -e "POSTGRES_PASSWORD=$password" postgres:17-alpine >/dev/null

ready=0
for _ in $(seq 1 40); do
  if docker exec "$container" pg_isready -h 127.0.0.1 -U "$user" -d "$db" >/dev/null 2>&1; then
    ready=1
    break
  fi
  sleep 1
done
if [[ "$ready" -ne 1 ]]; then
  echo "ISOLATED_STOCK_UAT=BLOCKED disposable_postgres_not_ready"
  exit 2
fi

compose=(docker compose -f docker-compose.staging.yml --env-file .env)
common=(--rm --no-deps -T
  -e "POSTGRES_DB=$db"
  -e "POSTGRES_USER=$user"
  -e "POSTGRES_PASSWORD=$password"
  -e "POSTGRES_HOST=$container"
  -e "POSTGRES_PORT=5432"
  -e "APP_ENV=test"
  -e "TELEGRAM_POLLING_ENABLED=false"
)

echo "TEST_DATABASE=EPHEMERAL_TMPFS"
echo "LIVE_STAGING_DATABASE=UNTOUCHED"
echo "MIGRATION=DISPOSABLE_DATABASE_ONLY"
"${compose[@]}" run "${common[@]}" app uv run --no-dev alembic upgrade head
"${compose[@]}" run "${common[@]}" app uv run --no-dev python \
  -m office_assistant.scripts.verify_isolated_stock_uat --apply
