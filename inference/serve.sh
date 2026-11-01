#!/usr/bin/env bash
# Run llama-server in the foreground for a profile. The harness and launchd wrap this script.
# Usage: inference/serve.sh <profile> [extra llama-server args...]
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
AIH_HOME="${AIH_HOME:-$(cd "$HERE/.." && pwd)}"
PROFILE="${1:-default}"; shift || true
ENV_FILE="$HERE/profiles/$PROFILE.env"
[ -f "$ENV_FILE" ] || { echo "no such profile: $PROFILE" >&2; exit 2; }
# shellcheck disable=SC1090
. "$ENV_FILE"

BIN="$AIH_HOME/llama.cpp/build/bin/llama-server"
[ -x "$BIN" ] || { echo "llama-server not built; run inference/build.sh" >&2; exit 1; }

HOST="${HOST:-0.0.0.0}"
THREADS="${THREADS:-$(sysctl -n hw.perflevel0.physicalcpu 2>/dev/null || echo 8)}"
mkdir -p "$AIH_HOME/slots"

ARGS=(
  -m "$MODELS_DIR/$MODEL_FILE"
  --alias "$ALIAS"
  -ngl 99 -fit off            # all layers on Metal; auto-fit mis-sizes qwen4exp
  -c "$CTX" --parallel "$PARALLEL" -kvu   # unified KV: any slot may use the full context
  -fa on -ctk "$CACHE_TYPE" -ctv "$CACHE_TYPE"
  -t "$THREADS" -tb "$THREADS"
  --slot-save-path "$AIH_HOME/slots"   # prefix caching is on by default; --cache-reuse is unsupported with DeltaNet state
  --jinja --metrics
  --host "$HOST" --port "$PORT"
  -lm mmap -lzm on            # mmap weights; read the n-gram table rows from SSD on demand
)
# Never --no-mmap / --mlock: the n-gram table shard must stay pageable.

if [ -n "${MTP_FILE:-}" ]; then
  ARGS+=(-md "$MODELS_DIR/$MTP_FILE" --spec-type draft-mtp --spec-draft-n-max "${DRAFT_N_MAX:-1}" -ngld 99)
fi

[ -n "${API_KEY:-}" ] && ARGS+=(--api-key "$API_KEY")

# shellcheck disable=SC2086
echo "==> $BIN ${ARGS[*]} ${EXTRA_ARGS:-} $*"
exec "$BIN" "${ARGS[@]}" ${EXTRA_ARGS:-} "$@"
