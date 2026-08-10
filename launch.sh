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

run_as_root() {
    if [ "${EUID}" -eq 0 ]; then
        "$@"
    elif command -v sudo >/dev/null 2>&1; then
        sudo "$@"
    else
        printf '%s\n' '错误：安装 Python 3 需要 root 权限或 sudo。' >&2
        return 1
    fi
}

install_python3() {
    printf '%s\n' '未检测到 python3，正在根据系统安装 Python 3 ...'
    if command -v apt-get >/dev/null 2>&1; then
        if ! run_as_root env DEBIAN_FRONTEND=noninteractive apt-get update || \
           ! run_as_root env DEBIAN_FRONTEND=noninteractive apt-get install -y python3; then
            return 1
        fi
    elif command -v apk >/dev/null 2>&1; then
        if ! run_as_root apk add --no-cache python3; then
            return 1
        fi
    elif command -v dnf >/dev/null 2>&1; then
        if ! run_as_root dnf install -y python3; then
            return 1
        fi
    elif command -v yum >/dev/null 2>&1; then
        if ! run_as_root yum install -y python3; then
            return 1
        fi
    else
        printf '%s\n' '错误：未识别 apt、apk、dnf 或 yum，无法自动安装 python3。' >&2
        return 1
    fi
    command -v python3 >/dev/null 2>&1
}

if ! command -v python3 >/dev/null 2>&1; then
    if ! install_python3 || ! command -v python3 >/dev/null 2>&1; then
        printf '%s\n' '错误：python3 自动安装失败，请手动安装后重试。' >&2
        exit 127
    fi
    printf '%s\n' "Python 3 已安装：$(python3 --version 2>&1)"
fi

download run.sh
download tui.py
download scripts.json
chmod 700 "${TEMP_DIR}/run.sh"
chmod 600 "${TEMP_DIR}/tui.py" "${TEMP_DIR}/scripts.json"
mv -f -- "${TEMP_DIR}/run.sh" "${CACHE_ROOT}/run.sh"
mv -f -- "${TEMP_DIR}/tui.py" "${CACHE_ROOT}/tui.py"
mv -f -- "${TEMP_DIR}/scripts.json" "${CACHE_ROOT}/scripts.json"

exec bash "${CACHE_ROOT}/run.sh" "$@"
