#!/usr/bin/env bash
# One-shot, staging-only visual catalog activation. No DB migration or volume deletion.
set -Eeuo pipefail

cd "$(dirname "$0")/.."
compose_file="docker-compose.staging.yml"
env_file=".env"

fail() {
  printf 'CATALOG_VISUAL_ACTIVATION=FAILED step=%s\n' "$1" >&2
  exit 2
}
[[ -f "$compose_file" && -f "$env_file" ]] || fail "missing_staging_config"
[[ "$(git branch --show-current)" == "main" ]] || fail "not_main_branch"
if [[ -n "$(git status --short --untracked-files=no)" ]]; then
  fail "tracked_worktree_not_clean"
fi
command -v docker >/dev/null || fail "docker_missing"
command -v python3 >/dev/null || fail "python3_missing"

# Preserve credentials and comments. Only these two explicitly approved
# feature flags are changed. The backup is never printed or committed.
umask 077
cp -p "$env_file" "$env_file.before-catalog-visual.bak"
python3 - "$env_file" <<'PY'
from pathlib import Path
import re
import sys

path = Path(sys.argv[1])
body = path.read_text(encoding="utf-8")
for key, value in {
    "VISUAL_SEMANTIC_ENABLED": "true",
    "VISUAL_SEMANTIC_MAX_REFERENCES": "200",
}.items():
    pattern = re.compile(rf"(?m)^[ \t]*(?:export[ \t]+)?{key}[ \t]*=.*$")
    replacement = f"{key}={value}"
    if pattern.search(body):
        body = pattern.sub(replacement, body)
    else:
        body = body.rstrip("\n") + f"\n{replacement}\n"
path.write_text(body, encoding="utf-8")
path.chmod(0o600)
PY

compose() {
  docker compose -f "$compose_file" --env-file "$env_file" "$@"
}

# Validate BEFORE replacing an already-running Telegram bot.
compose config --quiet || fail "compose_invalid"
compose build app || fail "app_build"

# Provision the local CLIP model explicitly and compute catalog reference
# vectors before changing the running app. Incoming photo bytes are never
# cached or sent to an external inference provider.
compose run --rm app \
  uv run --no-dev python \
  -m office_assistant.scripts.prepare_catalog_visual_references \
  --download-model --apply --limit 200 || fail "model_or_catalog_preparation"

compose up -d --no-deps --force-recreate app || fail "app_start"
compose exec -T app \
  uv run --no-dev python \
  -m office_assistant.scripts.diagnose_catalog_visual \
  --probe-model || fail "catalog_probe"

docker inspect tgstage_app \
  --format 'CATALOG_APP={{.State.Status}} RESTARTS={{.RestartCount}}' \
  || fail "app_not_running"
printf 'CATALOG_VISUAL_ACTIVATION=READY\n'
