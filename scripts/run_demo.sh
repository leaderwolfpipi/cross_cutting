#!/usr/bin/env bash
# Reproducible end-to-end demo with the footage in ../examples:
#   import → analyse → 2 rounds of remixes (4 platforms × 2) + dedup → one-click publish with injected faults.
# 抖音/快手 publish to the TEST-ONLY mock platform (mock_platforms/server.py) — no real accounts are touched.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
EXAMPLES="${EXAMPLES:-$(cd "$ROOT/../examples" && pwd)}"
PORT="${PORT:-8010}"; MOCK_PORT="${MOCK_PORT:-9100}"
cd "$ROOT/backend"
[ -d .venv ] || { python3.11 -m venv .venv && .venv/bin/pip install -q -r requirements.txt; }
[ -d "$ROOT/frontend/dist" ] || (cd "$ROOT/frontend" && npm install --no-audit --no-fund && npm run build)
if [ -z "${JIANYING_MCP_CMD:-}" ] && [ -x "$ROOT/../../jianying-me/scripts/mcp.sh" ]; then
  export JIANYING_MCP_CMD="$(cd "$ROOT/../../jianying-me" && pwd)/scripts/mcp.sh"
fi
mkdir -p "$ROOT/backend/data"
.venv/bin/python -m mock_platforms.server --port "$MOCK_PORT" > data/mock.log 2>&1 & MOCK=$!
env EMBEDDED_WORKER=true IMPORT_ROOTS="$EXAMPLES" \
    DOUYIN_API_BASE="http://127.0.0.1:$MOCK_PORT" KUAISHOU_API_BASE="http://127.0.0.1:$MOCK_PORT" \
    DOUYIN_CLIENT_KEY=demo DOUYIN_CLIENT_SECRET=demo KUAISHOU_APP_ID=demo KUAISHOU_APP_SECRET=demo \
    PUBLISH_POLL_INTERVAL_S=3 ACCOUNT_MIN_INTERVAL_S=0 PUBLISH_HTTP_TIMEOUT_S=5 PUBLISH_RECONCILE_GRACE_S=20 \
    PUBLIC_BASE_URL="http://127.0.0.1:$PORT" \
    .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port "$PORT" > data/api.log 2>&1 & API=$!
trap 'kill $API $MOCK 2>/dev/null' EXIT
until curl -s --noproxy '*' -o /dev/null "http://127.0.0.1:$PORT/health"; do sleep 1; done
.venv/bin/python scripts/demo_e2e.py --api "http://127.0.0.1:$PORT" --mock "http://127.0.0.1:$MOCK_PORT" \
    --examples "$EXAMPLES" --report data/demo_report.json
echo "Console: http://127.0.0.1:$PORT (API key louhaokey). Ctrl+C to stop."
wait $API
