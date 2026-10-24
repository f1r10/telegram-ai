#!/usr/bin/env bash
# Read-only preflight for docker-compose.staging.yml. Never inspects or changes qoder_*.
set -uo pipefail
errors=0
warnings=0
pass() { printf 'PASS %s\n' "$1"; }
fail() { printf 'BLOCK %s\n' "$1"; errors=$((errors+1)); }
warn() { printf 'WARN %s\n' "$1"; warnings=$((warnings+1)); }

if [[ ! -f docker-compose.staging.yml || ! -f docker-compose.yml ]]; then
  fail 'Run from the repository root with both Compose manifests present.'
  printf 'RESULT STAGING_BLOCKED | no changes made\n'
  exit 2
fi

if ! command -v docker >/dev/null 2>&1; then
  fail 'Docker CLI unavailable.'
elif ! command -v timeout >/dev/null 2>&1; then
  fail 'GNU timeout unavailable: cannot bound Docker requests safely.'
elif ! timeout 12 docker info --format '{{.OSType}}' 2>/dev/null | grep -qx linux; then
  fail 'Linux Docker engine unavailable.'
elif ! docker compose version >/dev/null 2>&1; then
  fail 'Docker Compose V2 unavailable.'
else
  pass 'Linux Docker engine and Compose V2 available.'
  containers="$(timeout 12 docker ps -a --format '{{.Names}}' 2>/dev/null)"
  containers_status=$?
  volumes="$(timeout 12 docker volume ls --format '{{.Name}}' 2>/dev/null)"
  volumes_status=$?
  if (( containers_status != 0 || volumes_status != 0 )); then
    fail 'Cannot inspect Docker containers or volumes.'
  else
    if printf '%s\n' "$containers" | grep -Eq '^tgstage_(app|miniapp|worker|postgres|qdrant|searxng|model_cache_init)$'; then
      fail 'tgstage container already exists; inspect owner before any startup.'
    else
      pass 'No existing tgstage container.'
    fi
    if printf '%s\n' "$volumes" | grep -Eq '^tgstage_(app_storage|model_cache|postgres_data|qdrant_data|searxng_cache)$'; then
      fail 'tgstage persistent volume already exists; inspect its data before startup.'
    else
      pass 'No existing tgstage persistent volume.'
    fi
  fi
  projects="$(timeout 12 docker compose ls --all --format json 2>/dev/null)"
  if [[ $? -ne 0 ]]; then
    warn 'Cannot inspect Compose projects; verify ownership before startup.'
  elif printf '%s\n' "$projects" | grep -Eq '"Name"[[:space:]]*:[[:space:]]*"tgstage_ai_assistand"'; then
    fail 'Staging project name already exists.'
  else
    pass 'Staging project name is unused.'
  fi
fi

if command -v ss >/dev/null 2>&1; then
  listeners="$(ss -H -ltn 2>/dev/null || true)"
  for port in 55435 16335 18095 18793; do
    if printf '%s\n' "$listeners" | grep -Eq "[:.]$port[[:space:]]"; then
      fail "Proposed staging localhost port $port is occupied."
    else
      pass "Staging localhost port $port appears free."
    fi
  done
else
  warn 'ss command unavailable; check staging ports 55435, 16335, 18095, 18793 manually.'
fi

if [[ -f .env ]]; then
  warn '.env exists: ensure staging-only credentials, no live Telegram token, and stock flags OFF.'
  if grep -Eiq '^MINIAPP_STOCK_(TRANSFER|ACTIONS|XLSX)_ENABLED[[:space:]]*=[[:space:]]*(true|1|yes)[[:space:]]*$' .env; then
    fail 'Staging stock write flag is ON.'
  fi
else
  warn 'No staging .env yet; create it privately only after collision checks.'
fi

if (( errors > 0 )); then
  printf 'RESULT STAGING_BLOCKED | errors=%s warnings=%s | no changes made\n' "$errors" "$warnings"
  exit 2
fi
printf 'RESULT STAGING_PRECHECK_OK | warnings=%s | no changes made\n' "$warnings"
