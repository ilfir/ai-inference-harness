#!/usr/bin/env bash
# Start the llama.cpp backend for a profile, from any directory.
# Usage: inference/start.sh [profile]        (default profile: default)
#        DRY_RUN=1 inference/start.sh        (print what would run, start nothing)
#
# Runs the harness preflight first. Right after a stop, the free-memory check fails
# because the old server's cached model pages still count as in use, even though macOS
# reports no pressure and releases them when the GPU wires its memory. If that is the ONLY
# failing check, this script starts with --force. Any other failure (missing model, port in
# use, wired limit too low) aborts so a real problem is never skipped.
set -euo pipefail

HERE="$(cd "$(dirname "$(realpath "$0")")" && pwd)"
export AIH_HOME="${AIH_HOME:-$(cd "$HERE/.." && pwd)}"
PROFILE="${1:-default}"
cd "$AIH_HOME"

PORT="$(sed -n 's/^PORT=//p' "$HERE/profiles/$PROFILE.env" | tr -d '"' | head -1)"
PORT="${PORT:-8080}"

if curl -s -m 3 "http://127.0.0.1:$PORT/health" 2>/dev/null | grep -q '"ok"'; then
  echo "backend already healthy on port $PORT; nothing to do (stop it first with: python3 -m harness stop)"
  [ "${DRY_RUN:-0}" = 1 ] || exit 0
fi

PF="$(python3 -m harness preflight "$PROFILE" 2>&1 || true)"
echo "$PF"
FAILS="$(printf '%s\n' "$PF" | grep '^❌' || true)"
FORCE=()
if [ -n "$FAILS" ]; then
  if [ "$(printf '%s\n' "$FAILS" | grep -vc 'free memory')" -eq 0 ]; then
    echo "==> only the free-memory check failed (stale file cache); starting with --force"
    FORCE=(--force)
  else
    echo "==> preflight has real failures; not starting:" >&2
    printf '%s\n' "$FAILS" >&2
    exit 1
  fi
fi

CMD=(python3 -m harness serve "$PROFILE" ${FORCE[@]+"${FORCE[@]}"})
if [ "${DRY_RUN:-0}" = 1 ]; then
  echo "DRY_RUN: would run: ${CMD[*]}"
  exit 0
fi
exec "${CMD[@]}"
