#!/usr/bin/env bash
# Health check script for DBT platform.
# Usage:
#   ./scripts/check-health.sh                         # default: https://genaidbt.top
#   ./scripts/check-health.sh http://127.0.0.1:8000   # inside web container network
set -euo pipefail

BASE_URL="${1:-https://genaidbt.top}"

echo "==> Liveness: ${BASE_URL}/health/"
curl -fsS "${BASE_URL}/health/" | python3 -m json.tool

echo "==> Readiness: ${BASE_URL}/health/ready/"
READY_CODE=$(curl -sS -o /tmp/dbt_ready.json -w "%{http_code}" "${BASE_URL}/health/ready/")
python3 -m json.tool < /tmp/dbt_ready.json
if [[ "${READY_CODE}" != "200" ]]; then
  echo "FAIL: readiness returned ${READY_CODE}" >&2
  exit 1
fi

if curl -fsS "${BASE_URL}/health/metrics/" -o /tmp/dbt_metrics.json 2>/dev/null; then
  echo "==> Metrics: ${BASE_URL}/health/metrics/"
  python3 -m json.tool < /tmp/dbt_metrics.json
else
  echo "==> Metrics endpoint not available (skipped)"
fi

echo "OK: health checks passed"
