#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND_PORT="${BWLI_BACKEND_PORT:-8787}"
FRONTEND_PORT="${BWLI_FRONTEND_PORT:-5173}"

cleanup() {
  if [[ -n "${BACKEND_PID:-}" ]]; then kill "$BACKEND_PID" 2>/dev/null || true; fi
  if [[ -n "${FRONTEND_PID:-}" ]]; then kill "$FRONTEND_PID" 2>/dev/null || true; fi
}
trap cleanup EXIT INT TERM

cd "$ROOT"
if [[ ! -d web/node_modules ]]; then
  npm --prefix web install
fi

BWLI_PROJECT_ROOT="$ROOT" uv run bwli serve --host 127.0.0.1 --port "$BACKEND_PORT" --reload &
BACKEND_PID=$!

npm --prefix web run dev -- --port "$FRONTEND_PORT" &
FRONTEND_PID=$!

cat <<EOF

BW Lineage Impact local dev is starting:
- Backend:  http://127.0.0.1:$BACKEND_PORT
- Frontend: http://127.0.0.1:$FRONTEND_PORT

Press Ctrl+C to stop both.
EOF

while true; do
  if ! kill -0 "$BACKEND_PID" 2>/dev/null; then
    set +e
    wait "$BACKEND_PID"
    status=$?
    set -e
    exit "$status"
  fi
  if ! kill -0 "$FRONTEND_PID" 2>/dev/null; then
    set +e
    wait "$FRONTEND_PID"
    status=$?
    set -e
    exit "$status"
  fi
  sleep 1
done
