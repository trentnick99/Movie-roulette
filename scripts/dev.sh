#!/usr/bin/env bash
# Development: backend with auto-reload on :8765 and the Vite dev server on :5173 (proxying /api and /ws).
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
trap 'kill $(jobs -p) 2>/dev/null || true' EXIT
(cd "$root/backend" && "$root/.venv/bin/python" -m app migrate && MR_DEV_ORIGIN=http://localhost:5173 \
  "$root/.venv/bin/uvicorn" "app.main:create_app" --factory --reload --port 8765) &
(cd "$root/frontend" && npm run dev)
