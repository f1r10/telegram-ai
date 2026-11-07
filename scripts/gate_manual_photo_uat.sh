#!/usr/bin/env bash
# Read-only technical gate before a HUMAN manually tests the existing Kali staging bot.
# Never deploys, migrates, starts a poller, changes .env or touches business data.
set -Eeuo pipefail

cd "$(dirname "$0")/.."
mode="${1:---full}"
case "$mode" in
  --full|--photo-only) ;;
  *) printf 'MANUAL_UAT_GATE=BLOCKED reason=invalid_mode\n' >&2; exit 2 ;;
esac

fail() {
  printf 'MANUAL_UAT_GATE=BLOCKED reason=%s\n' "$1" >&2
  exit 2
}

[[ -f .env && -f docker-compose.staging.yml ]] || fail "staging_files_missing"
for binary in git docker timeout; do
  command -v "$binary" >/dev/null 2>&1 || fail "required_command_missing"
done
[[ "$(git branch --show-current)" == "main" ]] || fail "not_on_main"
[[ -z "$(git status --porcelain --untracked-files=no)" ]] || fail "tracked_changes_exist"
if git ls-files --error-unmatch .env >/dev/null 2>&1; then
  fail "env_is_tracked"
fi

# Every potentially blocking operation has its own deadline and visible stage.
# Child stdout/stderr is hidden to avoid printing credentials or private paths.
compose_timed() {
  local seconds="$1"
  shift
  timeout --signal=TERM --kill-after=5s "$seconds" \
    docker compose -f docker-compose.staging.yml --env-file .env "$@"
}

run_check() {
  local name="$1"
  shift
  local result=0
  printf 'MANUAL_UAT_CHECK=%s status=RUNNING\n' "$name"
  "$@" >/dev/null 2>&1 || result=$?
  if (( result == 124 || result == 137 )); then
    fail "${name}_timeout"
  fi
  if (( result != 0 )); then
    fail "${name}_failed"
  fi
  printf 'MANUAL_UAT_CHECK=%s status=PASS\n' "$name"
}

run_check compose_configuration compose_timed 20 config --quiet
services=(app worker postgres qdrant searxng)
if [[ "$mode" == "--full" ]]; then
  services+=(miniapp)
fi

for service in "${services[@]}"; do
  container="tgstage_$service"
  project="$(timeout 12 docker inspect "$container" \
    --format '{{index .Config.Labels "com.docker.compose.project"}}' 2>/dev/null)" \
    || fail "staging_service_missing"
  [[ "$project" == "tgstage_ai_assistand" ]] || fail "unexpected_compose_project"
  state="$(timeout 12 docker inspect "$container" --format '{{.State.Status}}' 2>/dev/null)" \
    || fail "container_state_unavailable"
  [[ "$state" == "running" ]] || fail "container_not_running"
  health="$(timeout 12 docker inspect "$container" \
    --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' 2>/dev/null)" \
    || fail "container_health_unavailable"
  [[ "$health" == "healthy" || "$health" == "none" ]] || fail "container_unhealthy"
  # Dependencies must expose a real health signal; app and worker lack healthchecks.
  if [[ "$service" == "postgres" || "$service" == "qdrant" || "$service" == "searxng" ]]; then
    [[ "$health" == "healthy" ]] || fail "dependency_healthcheck_missing"
  fi
  printf 'MANUAL_UAT_SERVICE=%s state=running health=%s\n' "$service" "$health"
done

# Never echo environment, database credentials, bot tokens or private filenames.
run_check telegram_polling timeout --signal=TERM --kill-after=5s 15 \
  docker exec tgstage_app sh -c \
  'case "$TELEGRAM_POLLING_ENABLED" in true|TRUE|1|yes|YES) exit 0;; *) exit 1;; esac'

if [[ "$mode" == "--full" ]]; then
  run_check miniapp_https_configuration timeout --signal=TERM --kill-after=5s 15 \
    docker exec tgstage_miniapp sh -c \
    'case "$MINIAPP_ENABLED:$MINIAPP_PUBLIC_ORIGIN" in true:https://*|TRUE:https://*) exit 0;; *) exit 1;; esac'
fi

# Distinguish a reachable PostgreSQL service from a queryable database,
# a missing Alembic version table, and an outdated migration. These are all
# read-only SQL operations and no raw PostgreSQL errors or secrets are printed.
printf 'MANUAL_UAT_CHECK=schema_revision status=RUNNING\n'
# Use the exact container that was independently verified above. Avoid
# Compose service resolution here: the direct docker exec TCP probe is the
# known-good path on this host and cannot silently select another project.
sql_one() {
  local query="$1"
  timeout --signal=TERM --kill-after=5s 25 docker exec tgstage_postgres sh -c \
    'PGPASSWORD="${POSTGRES_PASSWORD:-}" psql -X -w -h 127.0.0.1 -p 5432 \
      -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
      -A -t -q -c "$1"' \
    sh "$query"
}
connectivity="$(sql_one 'SELECT 1' 2>/dev/null | tr -d '[:space:]')" \
  || fail "postgres_sql_connection_failed"
[[ "$connectivity" == "1" ]] || fail "postgres_sql_unexpected_response"
printf 'MANUAL_UAT_CHECK=postgres_sql status=PASS\n'

table="$(sql_one "SELECT COALESCE(to_regclass('alembic_version')::text, 'MISSING')" \
  2>/dev/null | tr -d '[:space:]')" || fail "schema_table_probe_failed"
[[ "$table" == "alembic_version" || "$table" == "public.alembic_version" ]] \
  || fail "schema_version_table_missing"

revision="$(sql_one 'SELECT version_num FROM alembic_version' 2>/dev/null \
  | tr -d '[:space:]')" || fail "schema_revision_query_failed"
if [[ "$revision" != "0023_photo_groups" ]]; then
  safe_revision="${revision:-EMPTY}"
  [[ "$safe_revision" =~ ^[A-Za-z0-9_.-]+$ ]] || safe_revision="UNEXPECTED"
  printf 'MANUAL_UAT_GATE=BLOCKED reason=schema_migration_required current=%s required=0023_photo_groups\n' \
    "$safe_revision" >&2
  exit 2
fi
printf 'MANUAL_UAT_CHECK=schema_revision status=PASS\n'

# A source checkout update changes host files only; ensure BOTH running images include the V1
# photo-group service before allowing the operator to test its callbacks.
for service in app worker; do
  container="tgstage_${service}"
  run_check "${service}_photo_group_runtime" timeout --signal=TERM --kill-after=5s 25 \
    docker exec "$container" /app/.venv/bin/python -c \
    'from office_assistant.application.photo_groups import PhotoGroupService; assert PhotoGroupService'
done

# These tools perform SELECTs, network health probes and an image catalog DRY RUN.
# Run them in the exact app container already verified above. Direct docker exec avoids
# Compose's service-exec wrapper and uv's project/lock resolution on this read-only gate.
# The image already contains the locked virtualenv at /app/.venv.
run_check live_readiness timeout --signal=TERM --kill-after=5s 65 \
  docker exec tgstage_app /app/.venv/bin/python -m office_assistant.scripts.check_readiness
run_check aggregate_photo_preflight timeout --signal=TERM --kill-after=5s 35 \
  docker exec tgstage_app /app/.venv/bin/python -m office_assistant.scripts.check_three_acceptance_gates
run_check visual_catalog_dry_run timeout --signal=TERM --kill-after=5s 80 \
  docker exec tgstage_app /app/.venv/bin/python -m office_assistant.scripts.prepare_visual_catalog

printf 'MANUAL_UAT_GATE=TECHNICAL_PASS mode=%s sha=%s\n' "$mode" "$(git rev-parse HEAD)"
printf 'MANUAL_UAT=HUMAN_ACCEPTANCE_REQUIRED\n'
printf 'MANUAL_UAT_WARN=REAL_PHOTO_ACCURACY_MOBILE_HTTPS_BACKUP_RESTORE_NOT_CERTIFIED\n'
