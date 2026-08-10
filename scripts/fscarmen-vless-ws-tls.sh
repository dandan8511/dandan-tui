#!/usr/bin/env bash
# Local launcher for the vendored fscarmen/sing-box v1.3.20 snapshot.
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "$0")" && pwd)"
UPSTREAM_SCRIPT="${SCRIPT_DIR}/fscarmen-sing-box-v1.3.20.sh"

if [ ! -r "$UPSTREAM_SCRIPT" ]; then
    printf '%s\n' "错误：未找到本地 fscarmen 脚本快照：${UPSTREAM_SCRIPT}" >&2
    exit 1
fi

printf '%s\n' '启动 VLESS + WS + TLS（fscarmen 本地快照）。'
printf '%s\n' '将复现上游主菜单 2（订阅 + Argo）并预选 i（vless + ws + tls）。'
printf '%s\n' '随后仍由上游流程交互询问端口、Argo、CDN、UUID 和节点名。'

exec bash "$UPSTREAM_SCRIPT" -C --yjl-tui-vless-ws-tls "$@"
