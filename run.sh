#!/usr/bin/env bash
set -Eeuo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "$0")" && pwd)"
export PYTHONUTF8=1
if ! command -v python3 >/dev/null 2>&1; then
    printf '%s\n' '错误：需要先安装 python3。' >&2
    exit 127
fi
exec python3 "$SCRIPT_DIR/tui.py" "$@"
