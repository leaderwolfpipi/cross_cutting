#!/usr/bin/env bash
# Single-machine start (macOS / Linux): API + embedded workers + built web console on http://127.0.0.1:${PORT:-8010}
# Production with several machines: run `uvicorn app.main:app` and `python -m app.jobs.worker` separately (see README).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PORT="${PORT:-8010}"
command -v ffmpeg >/dev/null || { echo "FFmpeg is required (macOS: brew install ffmpeg)"; exit 1; }

cd "$ROOT/backend"
if [ ! -d .venv ]; then
  python3.11 -m venv .venv
  .venv/bin/pip install -q --upgrade pip
fi
.venv/bin/pip install -q -r requirements.txt

if [ ! -d "$ROOT/frontend/dist" ]; then
  (cd "$ROOT/frontend" && npm install --no-audit --no-fund && npm run build)
fi

# 剪映 me render backend: auto-detected from /Applications, or point JIANYING_MCP_CMD at the dev tree
if [ -z "${JIANYING_MCP_CMD:-}" ] && [ -x "$ROOT/../../jianying-me/scripts/mcp.sh" ]; then
  export JIANYING_MCP_CMD="$(cd "$ROOT/../../jianying-me" && pwd)/scripts/mcp.sh"
fi
export EMBEDDED_WORKER="${EMBEDDED_WORKER:-true}"
echo "→ http://127.0.0.1:$PORT  (API key: ${API_KEYS:-dev-key-change-me}; set API_KEYS / SECRET_KEY in backend/.env for real use)"
exec .venv/bin/uvicorn app.main:app --host "${HOST:-127.0.0.1}" --port "$PORT"
