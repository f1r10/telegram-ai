#!/usr/bin/env bash
# Staging-only, reversible code rollout for manual Telegram photo acceptance.
# Never modifies .env, enables visual AI, runs migrations, or touches data volumes.
set -Eeuo pipefail

cd "$(dirname "$0")/.."
mode="${1:---check}"
case "$mode" in
  --check|--deploy) ;;
  *) printf 'PHOTO_STAGING=BLOCKED reason=invalid_mode\n' >&2; exit 2 ;;
esac

fail() {
  printf 'PHOTO_STAGING=BLOCKED reason=%s\n' "$1" >&2
  exit 2
}

[[ -f docker-compose.staging.yml && -f .env ]] || fail "staging_files_missing"
command -v git >/dev/null 2>&1 || fail "git_missing"
command -v docker >/dev/null 2>&1 || fail "docker_missing"
[[ "$(git branch --show-current)" == "main" ]] || fail "not_on_main"
[[ -z "$(git status --porcelain --untracked-files=no)" ]] || fail "tracked_changes_exist"
if git ls-files --error-unmatch .env >/dev/null 2>&1; then
  fail "env_is_tracked"
fi

compose() {
  docker compose -f docker-compose.staging.yml --env-file .env "$@"
}

compose config --quiet || fail "invalid_compose"
# Avoid creating or replacing an unrelated stack or an unstarted installation.
project="$(docker inspect tgstage_app \
  --format '{{index .Config.Labels "com.docker.compose.project"}}' 2>/dev/null)" \
  || fail "staging_app_not_found"
[[ "$project" == "tgstage_ai_assistand" ]] || fail "unexpected_docker_project"

previous="$(git rev-parse HEAD)"
printf 'PHOTO_STAGING=CHECKED current_sha=%s mode=%s\n' "$previous" "$mode"
if [[ "$mode" == "--check" ]]; then
  printf 'PHOTO_STAGING=NO_CHANGES\n'
  exit 0
fi

# Fast-forward only, never overwrite local changes or choose a new branch.
git fetch --quiet origin main || fail "git_fetch_failed"
target="$(git rev-parse refs/remotes/origin/main)"
git merge-base --is-ancestor "$previous" "$target" || fail "not_fast_forward"
git merge --ff-only refs/remotes/origin/main || fail "git_update_failed"
[[ -z "$(git status --porcelain --untracked-files=no)" ]] || fail "post_pull_not_clean"
compose config --quiet || fail "updated_compose_invalid"

# A new photo-group commit needs additive migration 0023. Check the actual
# *staging* PostgreSQL version before replacing running bot/worker containers.
# The check does not print credentials, run migrations, or touch business rows.
required_revision="0023_photo_groups"
current_revision="$(compose exec -T postgres sh -c \
  'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -A -t -q -c "SELECT version_num FROM alembic_version"' \
  | tr -d '[:space:]')" || fail "schema_revision_probe_failed"
[[ "$current_revision" == "$required_revision" ]] || fail "schema_migration_required"

# Stage only the two existing application processes. Build before restart.
compose build app worker || fail "image_build_failed"
compose up -d --no-deps --force-recreate app worker || fail "app_worker_start_failed"
compose exec -T app uv run --no-dev python -m office_assistant.scripts.check_readiness \
  || fail "readiness_check_failed"
# Aggregate read-only counts, including active human-confirmed photo references.
compose exec -T app uv run --no-dev python \
  -m office_assistant.scripts.check_three_acceptance_gates \
  || fail "photo_reference_preflight_failed"
# Count indexable local photos and PDF artwork without running or downloading a model.
compose exec -T app uv run --no-dev python \
  -m office_assistant.scripts.prepare_visual_catalog \
  || fail "visual_catalog_dry_run_failed"

# A printed container state is not a successful readiness check.
# Fail closed when an app/worker was recreated but exited or is restarting.
for service in app worker; do
  container="tgstage_${service}"
  status="$(docker inspect "$container" --format '{{.State.Status}}' 2>/dev/null)" \
    || fail "${service}_inspection_failed"
  [[ "$status" == "running" ]] || fail "${service}_not_running"
  health="$(docker inspect "$container" --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' 2>/dev/null)" \
    || fail "${service}_health_probe_failed"
  [[ "$health" == "none" || "$health" == "healthy" ]] || fail "${service}_unhealthy"
  docker inspect "$container" --format "PHOTO_${service^^}={{.State.Status}} RESTARTS={{.RestartCount}}" \
    || fail "${service}_inspection_failed"
done
printf 'PHOTO_STAGING=DEPLOYED previous_sha=%s deployed_sha=%s\n' \
  "$previous" "$(git rev-parse HEAD)"
printf 'PHOTO_STAGING=MANUAL_PHOTO_UAT_REQUIRED\n'
