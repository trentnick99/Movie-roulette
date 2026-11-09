#!/usr/bin/env bash
# Browser end-to-end test of a whole game night against a real server, real FFmpeg and a fake TMDB.
# Needs: .venv with backend requirements, frontend dependencies, FFmpeg, and Playwright's Chromium
# (npx playwright install chromium) or CHROMIUM_PATH pointing at a Chromium binary.
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
work="$(mktemp -d)"
trap 'kill $(jobs -p) 2>/dev/null || true; rm -rf "$work"' EXIT
py="${PYTHON:-$root/.venv/bin/python}"
port="${MR_E2E_PORT:-8777}"

"$root/scripts/make_test_movies.sh" "$work/Movies"
(cd "$root/frontend" && [ -f dist/index.html ] || npm run build)
(cd "$root/backend" && "$py" -m tests.fake_tmdb_server 8799) &
(cd "$root/backend" && MR_DATA_DIR="$work/data" MR_PORT="$port" MR_TMDB_API_BASE=http://127.0.0.1:8799/3 \
  MR_TMDB_IMAGE_BASE=http://127.0.0.1:8799/t/p "$py" -m app --no-browser) &
for _ in $(seq 60); do curl -fs "http://127.0.0.1:$port/api/health" >/dev/null && break; sleep 0.5; done
cd "$root/frontend"
MR_E2E_URL="http://127.0.0.1:$port" MR_E2E_MOVIES="$work/Movies" npx playwright test
