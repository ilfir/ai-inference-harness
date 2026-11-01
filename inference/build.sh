#!/usr/bin/env bash
# Build llama.cpp (Metal) at the pinned commit into $AIH_HOME/llama.cpp.
# Usage: inference/build.sh [--commit <sha>] [--clean]
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
AIH_HOME="${AIH_HOME:-$(cd "$HERE/.." && pwd)}"
SRC="$AIH_HOME/llama.cpp"
COMMIT="$(tr -d '[:space:]' < "$HERE/LLAMA_CPP_COMMIT")"
CLEAN=0

while [ $# -gt 0 ]; do
  case "$1" in
    --commit) COMMIT="$2"; shift 2 ;;
    --clean)  CLEAN=1; shift ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done

export PATH="$HOME/.local/bin:$PATH"

if ! command -v cmake >/dev/null 2>&1; then
  if command -v uv >/dev/null 2>&1; then
    echo "==> installing cmake with uv"
    uv tool install cmake
  else
    echo "cmake not found; install uv (https://docs.astral.sh/uv/) or cmake first" >&2
    exit 1
  fi
fi

# Shallow fetch of just the pinned commit: a few MB instead of the full history.
if [ ! -d "$SRC/.git" ]; then
  git init --quiet "$SRC"
  git -C "$SRC" remote add origin https://github.com/ggml-org/llama.cpp
fi
if git -C "$SRC" cat-file -e "$COMMIT^{commit}" 2>/dev/null; then
  echo "==> llama.cpp $COMMIT already present"
  git -C "$SRC" checkout --quiet --detach "$COMMIT"
else
  echo "==> fetching llama.cpp $COMMIT"
  git -C "$SRC" fetch --quiet --depth 1 origin "$COMMIT"
  git -C "$SRC" checkout --quiet --detach FETCH_HEAD
fi
git -C "$SRC" log -1 --format='    %H %cd %s'

[ "$CLEAN" = 1 ] && rm -rf "$SRC/build"

NPROC="$(sysctl -n hw.perflevel0.physicalcpu 2>/dev/null || sysctl -n hw.ncpu)"

echo "==> configuring (Metal, static)"
cmake -S "$SRC" -B "$SRC/build" \
  -DCMAKE_BUILD_TYPE=Release \
  -DGGML_METAL=ON \
  -DGGML_METAL_EMBED_LIBRARY=ON \
  -DGGML_NATIVE=ON \
  -DLLAMA_CURL=OFF \
  -DBUILD_SHARED_LIBS=OFF

echo "==> building with $NPROC jobs"
cmake --build "$SRC/build" -j "$NPROC" \
  --target llama-server llama-cli llama-bench llama-gguf-split

"$SRC/build/bin/llama-server" --version 2>&1 | head -2
echo "BUILD_OK"
