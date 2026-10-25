#!/usr/bin/env bash
# Real Mini App HTTP smoke on container loopback only. NOT a mobile/HTTPS UAT.
set -Eeuo pipefail
umask 077
cd "$(dirname "$0")/.."

test -f docker-compose.staging.yml && test -f .env || {
  echo "MINIAPP_LOCAL_SMOKE=BLOCKED missing_staging_config"
  exit 2
}
compose=(docker compose -f docker-compose.staging.yml --env-file .env)
tmp="$(mktemp)"
cid=""
cleanup() {
  if [[ -n "$cid" ]]; then
    docker rm -f "$cid" >/dev/null 2>&1 || true
  fi
  rm -f -- "$tmp"
}
trap cleanup EXIT

# No published host/WAN ports; separate transient process. The fake HTTPS
# origin is for same-origin parsing only. It is NEVER sent to Telegram.
"${compose[@]}" build miniapp >/dev/null
cid="$("${compose[@]}" run --rm --no-deps -T -d \
  -e MINIAPP_ENABLED=true \
  -e MINIAPP_PUBLIC_ORIGIN=https://miniapp-local-smoke.invalid \
  -e MINIAPP_CONTAINER_BIND=false \
  -e MINIAPP_STOCK_TRANSFER_ENABLED=false \
  -e MINIAPP_STOCK_ACTIONS_ENABLED=false \
  -e MINIAPP_STOCK_XLSX_ENABLED=false \
  miniapp)"

for _ in $(seq 1 30); do
  if docker exec "$cid" sh -c \
      'uv run --no-dev python -m office_assistant.scripts.smoke_miniapp --port "$MINIAPP_LOCAL_PORT"' \
      >"$tmp" 2>/dev/null; then
    cat "$tmp"
    echo "MINIAPP_LOCAL_SMOKE=PASS"
    echo "TELEGRAM_REAL_MOBILE_UAT=NOT_TESTED"
    exit 0
  fi
  sleep 2
done

echo "MINIAPP_LOCAL_SMOKE=BLOCKED"
echo "Review transient Mini App startup prerequisites; no live port was published."
exit 2
