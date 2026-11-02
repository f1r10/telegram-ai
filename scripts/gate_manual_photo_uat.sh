#!/usr/bin/env bash
# Read-only technical gate before a HUMAN manually tests the existing Kali staging bot.
# Never deploys, migrates, starts a poller, changes .env or touches business data.
set -Eeuo pipefail

cd "$(dirname "$0")/.."
mode="\${1:---full}"
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

compose() {
  timeout 90 docker compose -f docker-compose.staging.yml --env-file .env "$@"
}

compose config --quiet >/dev/null 2>&1 || fail "invalid_staging_compose"
services=(app worker postgres qdrant searxng)
if [[ "$mode" == "--full" ]]; then
  services+=(miniapp)
fi

for service in "\${services[@]}"; do
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
timeout 12 docker exec tgstage_app sh -c \
  'case "$TELEGRAM_POLLING_ENABLED" in true|TRUE|1|yes|YES) exit 0;; *) exit 1;; esac' \
  >/dev/null 2>&1 || fail "telegram_polling_disabled"

if [[ "$mode" == "--full" ]]; then
  timeout 12 docker exec tgstage_miniapp sh -c \
    'case "$MINIAPP_ENABLED:$MINIAPP_PUBLIC_ORIGIN" in true:https://*|TRUE:https://*) exit 0;; *) exit 1;; esac' \
    >/dev/null 2>&1 || fail "miniapp_https_not_configured"
fi

revision="$(compose exec -T postgres sh -c \
  'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -A -t -q -c "SELECT version_num FROM alembic_version"' \
  2>/dev/null | tr -d '[:space:]')" || fail "schema_probe_failed"
[[ "$revision" == "0023_photo_groups" ]] || fail "schema_migration_required"

# These tools perform SELECTs, network health probes and an image catalog DRY RUN.
# Their reports are aggregate; do not forward logs containing private source data.
compose exec -T app uv run --no-dev python -m office_assistant.scripts.check_readiness \
  >/dev/null 2>&1 || fail "live_readiness_failed"
compose exec -T app uv run --no-dev python \
  -m office_assistant.scripts.check_three_acceptance_gates \
  >/dev/null 2>&1 || fail "aggregate_photo_preflight_failed"
compose exec -T app uv run --no-dev python \
  -m office_assistant.scripts.prepare_visual_catalog \
  >/dev/null 2>&1 || fail "visual_catalog_dry_run_failed"

printf 'MANUAL_UAT_GATE=TECHNICAL_PASS mode=%s sha=%s\n' "$mode" "$(git rev-parse HEAD)"
printf 'MANUAL_UAT=HUMAN_ACCEPTANCE_REQUIRED\n'
printf 'MANUAL_UAT_WARN=REAL_PHOTO_ACCURACY_MOBILE_HTTPS_BACKUP_RESTORE_NOT_CERTIFIED\n'
