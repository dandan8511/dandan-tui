#!/usr/bin/env bash
set -Eeuo pipefail

REF="${YJL_TUI_REF:-main}"
BASE_URL="https://raw.githubusercontent.com/dandan8511/dandan-tui/${REF}"
CACHE_ROOT="${XDG_CACHE_HOME:-${HOME}/.cache}/dandan-tui"
mkdir -p -- "$CACHE_ROOT"
TEMP_DIR="$(mktemp -d "${CACHE_ROOT}.download.XXXXXX")"

cleanup() {
    rm -rf -- "$TEMP_DIR"
}
trap cleanup EXIT

download() {
    local name="$1"
    if command -v curl >/dev/null 2>&1; then
        curl -fsSL --retry 3 --connect-timeout 15 --max-time 180 \
            "${BASE_URL}/${name}" -o "${TEMP_DIR}/${name}"
    elif command -v wget >/dev/null 2>&1; then
        wget -qO "${TEMP_DIR}/${name}" "${BASE_URL}/${name}"
    else
        printf '%s\n' '错误：需要 curl 或 wget。' >&2
        exit 127
    fi
}

command -v bash >/dev/null 2>&1 || { printf '%s\n' '错误：需要 bash。' >&2; exit 127; }
command -v python3 >/dev/null 2>&1 || { printf '%s\n' '错误：需要 python3。' >&2; exit 127; }

download run.sh
download tui.py
download scripts.json
chmod 700 "${TEMP_DIR}/run.sh"
chmod 600 "${TEMP_DIR}/tui.py" "${TEMP_DIR}/scripts.json"
mv -f -- "${TEMP_DIR}/run.sh" "${CACHE_ROOT}/run.sh"
mv -f -- "${TEMP_DIR}/tui.py" "${CACHE_ROOT}/tui.py"
mv -f -- "${TEMP_DIR}/scripts.json" "${CACHE_ROOT}/scripts.json"

exec bash "${CACHE_ROOT}/run.sh" "$@"
