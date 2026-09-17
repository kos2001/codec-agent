#!/usr/bin/env bash
# Build the frontend (if needed) and serve the workbench on http://127.0.0.1:8013
set -euo pipefail
cd "$(dirname "$0")/.."
if [ ! -d frontend/node_modules ]; then (cd frontend && npm install --no-audit --no-fund); fi
if [ "${1:-}" = "--dev" ]; then
  .venv/bin/uvicorn api.server:app --host 127.0.0.1 --port 8013 &
  trap 'kill %1' EXIT
  (cd frontend && npm run dev)          # http://127.0.0.1:5175, proxies /api
else
  (cd frontend && npm run build)
  exec .venv/bin/uvicorn api.server:app --host 127.0.0.1 --port 8013
fi
