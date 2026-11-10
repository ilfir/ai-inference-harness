#!/usr/bin/env bash
# Fetch the model files for a profile (weights, MTP head, vision projector) into its MODELS_DIR.
# Reuses files already in the Hugging Face cache (symlinks) before downloading.
# Usage: inference/download.sh <profile>
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
PROFILE="${1:-default}"
ENV_FILE="$HERE/profiles/$PROFILE.env"
[ -f "$ENV_FILE" ] || { echo "no such profile: $PROFILE" >&2; exit 2; }
# shellcheck disable=SC1090
. "$ENV_FILE"

export PATH="$HOME/.local/bin:$PATH"
# Plain HTTP: the xet transfer path buffered whole files in RAM after an interruption.
export HF_HUB_DISABLE_XET="${HF_HUB_DISABLE_XET:-1}"
HF_CACHE="${HF_HOME:-$HOME/.cache/huggingface}/hub"

command -v hf >/dev/null 2>&1 || { echo "hf CLI not found (uv tool install huggingface_hub)" >&2; exit 1; }
mkdir -p "$MODELS_DIR"

# link_from_cache <repo> <relative file>: symlink a completed file from the HF cache if present
link_from_cache() {
  local repo="$1" rel="$2" cached
  cached="$(ls -d "$HF_CACHE/models--${repo//\//--}"/snapshots/*/"$rel" 2>/dev/null | head -1 || true)"
  if [ -n "$cached" ] && [ -e "$cached" ] && [ ! -e "$MODELS_DIR/$rel" ]; then
    mkdir -p "$(dirname "$MODELS_DIR/$rel")"
    ln -s "$cached" "$MODELS_DIR/$rel"
    echo "==> reused from HF cache: $rel"
  fi
}

fetch() {
  local repo="$1" include="$2"
  echo "==> $repo  [$include]"
  hf download "$repo" --include "$include" --local-dir "$MODELS_DIR"
}

# Single-file includes can be satisfied from the cache; globs always go through hf (it resumes).
case "$MODEL_INCLUDE" in *'*'*) ;; *) link_from_cache "$MODEL_REPO" "$MODEL_INCLUDE" ;; esac
[ -e "$MODELS_DIR/$MODEL_FILE" ] && [ "$MODEL_INCLUDE" = "$MODEL_FILE" ] || fetch "$MODEL_REPO" "$MODEL_INCLUDE"

if [ -n "${MTP_FILE:-}" ]; then
  link_from_cache "$MTP_REPO" "$MTP_FILE"
  [ -e "$MODELS_DIR/$MTP_FILE" ] || fetch "$MTP_REPO" "$MTP_FILE"
fi

if [ -n "${MMPROJ_FILE:-}" ]; then
  link_from_cache "$MMPROJ_REPO" "$MMPROJ_FILE"
  [ -e "$MODELS_DIR/$MMPROJ_FILE" ] || fetch "$MMPROJ_REPO" "$MMPROJ_FILE"
fi

echo "DOWNLOAD_OK $PROFILE"
