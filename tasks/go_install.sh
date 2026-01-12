#!/usr/bin/env bash
set -euo pipefail

if [ $# -ne 2 ]; then
  echo "usage: $0 <build-dir> <go-module-path@version>" >&2
  exit 1
fi

GO_MODULE="$1"
BUILD_DIR="$2"

# cross-platform md5 (macOS + Linux)
if command -v md5 >/dev/null 2>&1; then
  HASH="$(printf '%s' "$GO_MODULE" | md5)"
elif command -v md5sum >/dev/null 2>&1; then
  HASH="$(printf '%s' "$GO_MODULE" | md5sum | cut -d' ' -f1)"
else
  echo "error: neither md5 nor md5sum found" >&2
  exit 1
fi

export CGO_ENABLED="0"
go install "$GO_MODULE"

mkdir -p "$BUILD_DIR"

OUTPUT="$BUILD_DIR/$HASH.md5"
echo "$GO_MODULE" > "$OUTPUT"