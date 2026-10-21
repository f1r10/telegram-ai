#!/usr/bin/env bash
# Read-only Windows/WSL2 + Docker staging readiness check.
# Does not print credentials, change volumes, or start containers.
set -uo pipefail
failures=0
warnings=0
pass() { printf 'PASS %s\n' "$1"; }
warn() { printf 'WARN %s\n' "$1"; warnings=$((warnings + 1)); }
fail() { printf 'FAIL %s\n' "$1"; failures=$((failures + 1)); }

if [[ ! -f docker-compose.yml || ! -f .env.example ]]; then
  fail 'Run from the cloned telegram-ai repository root.'
  printf 'RESULT BLOCKED | no changes made\n'
  exit 2
fi
if [[ "$(uname -s)" != "Linux" ]]; then
  fail 'Run inside WSL2 Ubuntu, not PowerShell or Git Bash.'
elif grep -Eqi 'microsoft.*wsl2|wsl2.*microsoft' /proc/sys/kernel/osrelease 2>/dev/null; then
  pass 'WSL2 kernel detected.'
elif grep -qi microsoft /proc/sys/kernel/osrelease 2>/dev/null; then
  fail 'WSL1 detected; WSL2 is required.'
else
  warn 'Linux detected, but not identified as WSL2; inspect wsl -l -v on Windows.'
fi
case "$PWD" in
  /mnt/*) warn 'Repository is on a Windows drive; use ~/telegram-ai-staging inside WSL.' ;;
  *) pass 'Repository is on a Linux-native path.' ;;
esac
if command -v git >/dev/null 2>&1; then
  pass 'Git available.'
else
  fail 'Git is missing in WSL.'
fi
if ! command -v docker >/dev/null 2>&1; then
  fail 'Docker CLI missing; enable Docker Desktop WSL integration.'
else
  pass 'Docker CLI available.'
  if command -v timeout >/dev/null 2>&1; then
    if engine="$(timeout 12 docker info --format '{{.OSType}}' 2>/dev/null)"; then
      if [[ "$engine" == linux ]]; then
        pass 'Linux Docker engine available.'
      else
        fail 'Docker engine is not running Linux containers.'
      fi
    else
      fail 'Docker engine unavailable; start Docker Desktop and enable WSL integration.'
    fi
    existing="$(timeout 12 docker ps -a --format '{{.Names}}' 2>/dev/null || true)"
    if printf '%s\n' "$existing" | grep -Eq '^qoder_(app|worker|postgres|qdrant|miniapp|searxng|model_cache_init)$'; then
      warn 'Existing qoder_* containers found: confirm owner before starting staging.'
    else
      pass 'No fixed qoder_* container-name collision found.'
    fi
    volumes="$(timeout 12 docker volume ls --format '{{.Name}}' 2>/dev/null || true)"
    if printf '%s\n' "$volumes" | grep -Eq '^qoder_(app_storage|postgres_data|qdrant_data|model_cache|searxng_cache)$'; then
      warn 'Existing qoder_* volumes found: do NOT replace or delete these.'
    else
      pass 'No fixed qoder_* volume-name collision found.'
    fi
  else
    warn 'GNU timeout missing; daemon and container checks skipped.'
  fi
  if docker compose version >/dev/null 2>&1; then
    pass 'Docker Compose V2 available.'
  else
    fail 'Docker Compose V2 missing.'
  fi
fi
if [[ -f .env ]]; then
  pass 'Local .env exists; its contents are not displayed.'
  for setting in MINIAPP_STOCK_TRANSFER_ENABLED MINIAPP_STOCK_ACTIONS_ENABLED MINIAPP_STOCK_XLSX_ENABLED; do
    if grep -Eiq "^${setting}=[[:space:]]*(true|1|yes)[[:space:]]*$" .env; then
      fail 'A stock-write flag is ON; turn off all stock writes before testing.'
    fi
  done
  if grep -Eiq '^TELEGRAM_POLLING_ENABLED=[[:space:]]*(true|1|yes)[[:space:]]*$' .env; then
    warn 'Telegram polling is ON; use a test bot and check no competing poller.'
  fi
  if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
    if docker compose --env-file .env config --quiet >/dev/null 2>&1; then
      pass 'Compose configuration parses.'
    else
      fail 'Compose configuration is invalid. Inspect locally; do not share .env.'
    fi
  fi
else
  warn 'No .env yet; create it privately after preflight.'
fi
if command -v df >/dev/null 2>&1; then
  free_kib="$(df -Pk . 2>/dev/null | awk 'NR==2 {print $4}')"
  if [[ "$free_kib" =~ ^[0-9]+$ ]]; then
    if (( free_kib < 5 * 1024 * 1024 )); then
      warn 'Less than 5 GiB disk free; allow more for model and documents.'
    else
      pass 'At least 5 GiB staging filesystem free.'
    fi
  fi
fi
if (( failures == 0 )); then
  printf 'RESULT PREFLIGHT_OK | warnings=%s | no changes made\n' "$warnings"
  exit 0
fi
printf 'RESULT BLOCKED | failures=%s warnings=%s | no changes made\n' "$failures" "$warnings"
exit 2
