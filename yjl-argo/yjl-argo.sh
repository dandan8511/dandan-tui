#!/usr/bin/env bash
# yjl-argo: standalone Cloudflare Tunnel manager. It deliberately does not install sing-box.
set -Eeuo pipefail

APP_NAME='YJL Argo Tunnel'
APP_DIR="${YJL_ARGO_CONFIG_DIR:-/etc/yjl-argo}"
CONFIG_FILE="${APP_DIR}/config.env"
LOG_FILE="${YJL_ARGO_LOG_FILE:-/var/log/yjl-argo.log}"
BIN_DIR="${YJL_ARGO_BIN_DIR:-/usr/local/bin}"
CLOUDFLARED="${YJL_ARGO_CLOUDFLARED:-${BIN_DIR}/cloudflared}"
SERVICE_NAME='yjl-argo'
METRICS_ADDR='127.0.0.1:20241'
SYSTEMD_UNIT="/etc/systemd/system/${SERVICE_NAME}.service"
OPENRC_UNIT="/etc/init.d/${SERVICE_NAME}"
SCRIPT_PATH="${YJL_ARGO_SCRIPT_PATH:-$0}"

COLOR=1
[[ ! -t 1 || "${NO_COLOR:-}" != '' ]] && COLOR=0
if (( COLOR )); then
  C_RED=$'\033[31m'; C_GREEN=$'\033[32m'; C_YELLOW=$'\033[33m'; C_BLUE=$'\033[36m'; C_RESET=$'\033[0m'
else
  C_RED=''; C_GREEN=''; C_YELLOW=''; C_BLUE=''; C_RESET=''
fi

MODE=''
TARGET=''
TOKEN=''
PROTOCOL='http2'
QUICK_URL=''
PUBLIC_HOSTNAME=''
PUBLIC_PATH=''

say() { printf '%b\n' "$*"; }
ok() { say "${C_GREEN}[成功]${C_RESET} $*"; }
warn() { say "${C_YELLOW}[注意]${C_RESET} $*"; }
err() { say "${C_RED}[错误]${C_RESET} $*" >&2; }
line() { say "${C_BLUE}------------------------------------------------------------${C_RESET}"; }
pause() { read -r -p '按回车键继续...' _; }
command_exists() { command -v "$1" >/dev/null 2>&1; }

require_root() {
  if [[ ${EUID} -ne 0 ]]; then
    err '此操作需要 root。请使用 root 登录后执行 yjl，或使用 sudo yjl。'
    return 1
  fi
}

init_type() {
  if command_exists systemctl && [[ -d /run/systemd/system ]]; then
    printf 'systemd\n'
  elif command_exists rc-service && command_exists rc-update; then
    printf 'openrc\n'
  else
    printf 'unknown\n'
  fi
}

load_config() {
  MODE=''; TARGET=''; TOKEN=''; PROTOCOL='http2'; QUICK_URL=''; PUBLIC_HOSTNAME=''; PUBLIC_PATH=''
  [[ -r "$CONFIG_FILE" ]] || return 0
  # The file is generated locally with restrictive permissions, never shell-sourced.
  while IFS='=' read -r key value; do
    case "$key" in
      MODE) MODE=$value ;;
      TARGET) TARGET=$value ;;
      TOKEN) TOKEN=$value ;;
      PROTOCOL) PROTOCOL=$value ;;
      QUICK_URL) QUICK_URL=$value ;;
      PUBLIC_HOSTNAME) PUBLIC_HOSTNAME=$value ;;
      PUBLIC_PATH) PUBLIC_PATH=$value ;;
    esac
  done < "$CONFIG_FILE"
}

write_config() {
  require_root || return
  install -d -m 700 "$APP_DIR"
  umask 077
  {
    printf 'MODE=%s\n' "$MODE"
    printf 'TARGET=%s\n' "$TARGET"
    printf 'TOKEN=%s\n' "$TOKEN"
    printf 'PROTOCOL=%s\n' "$PROTOCOL"
    printf 'QUICK_URL=%s\n' "$QUICK_URL"
    printf 'PUBLIC_HOSTNAME=%s\n' "$PUBLIC_HOSTNAME"
    printf 'PUBLIC_PATH=%s\n' "$PUBLIC_PATH"
  } > "$CONFIG_FILE"
  chmod 600 "$CONFIG_FILE"
}

valid_target() {
  [[ "$1" =~ ^https?://[^[:space:]]+$ ]]
}

valid_hostname() {
  [[ "$1" =~ ^[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?(\.[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+$ ]]
}

valid_public_path() {
  [[ -z "$1" || "$1" =~ ^/[^[:space:]]*$ ]]
}

install_deps() {
  if command_exists apt-get; then
    DEBIAN_FRONTEND=noninteractive apt-get update
    DEBIAN_FRONTEND=noninteractive apt-get install -y ca-certificates curl
  elif command_exists apk; then
    apk add --no-cache ca-certificates curl
  else
    err '只支持 apt（Debian/Ubuntu）或 apk（Alpine）安装依赖。'
    return 1
  fi
}

cloudflared_arch() {
  case "$(uname -m)" in
    x86_64|amd64) printf 'amd64\n' ;;
    aarch64|arm64) printf 'arm64\n' ;;
    armv7l|armv6l|armhf) printf 'arm\n' ;;
    *) return 1 ;;
  esac
}

install_cloudflared() {
  require_root || return
  if [[ -x "$CLOUDFLARED" ]]; then
    ok "已安装 cloudflared：$($CLOUDFLARED --version 2>/dev/null | head -n 1 || true)"
    return 0
  fi
  install_deps
  local arch url temporary
  arch=$(cloudflared_arch) || { err "不支持的 CPU 架构：$(uname -m)"; return 1; }
  url="https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-${arch}"
  temporary=$(mktemp)
  say "下载 cloudflared (${arch})..."
  curl -fL --retry 3 --connect-timeout 15 "$url" -o "$temporary"
  install -d -m 755 "$(dirname "$CLOUDFLARED")"
  install -m 755 "$temporary" "$CLOUDFLARED"
  rm -f "$temporary"
  "$CLOUDFLARED" --version
  ok 'cloudflared 安装完成。'
}

ensure_cloudflared() {
  [[ -x "$CLOUDFLARED" ]] || install_cloudflared
}

service_exists() {
  case "$(init_type)" in
    systemd) [[ -e "$SYSTEMD_UNIT" ]] ;;
    openrc) [[ -e "$OPENRC_UNIT" ]] ;;
    *) return 1 ;;
  esac
}

write_service() {
  require_root || return
  local init
  init=$(init_type)
  case "$init" in
    systemd)
      cat > "$SYSTEMD_UNIT" <<EOF
[Unit]
Description=YJL managed Cloudflare Tunnel
Wants=network-online.target
After=network-online.target

[Service]
Type=simple
ExecStart=${SCRIPT_PATH} run
Restart=always
RestartSec=5
NoNewPrivileges=true

[Install]
WantedBy=multi-user.target
EOF
      systemctl daemon-reload
      ;;
    openrc)
      cat > "$OPENRC_UNIT" <<EOF
#!/sbin/openrc-run
name="YJL managed Cloudflare Tunnel"
description="Cloudflare Tunnel managed by yjl"
command="${SCRIPT_PATH}"
command_args="run"
supervisor="supervise-daemon"
pidfile="/run/${SERVICE_NAME}.pid"
output_log="${LOG_FILE}"
error_log="${LOG_FILE}"
respawn_delay=5
depend() {
  need net
  after firewall
}
EOF
      chmod 755 "$OPENRC_UNIT"
      ;;
    *) err '未检测到 systemd 或 OpenRC，无法注册开机自启。'; return 1 ;;
  esac
}

service_enable_start() {
  local init
  init=$(init_type)
  case "$init" in
    systemd) systemctl enable "$SERVICE_NAME"; systemctl restart "$SERVICE_NAME" ;;
    openrc) rc-update add "$SERVICE_NAME" default >/dev/null 2>&1 || true; rc-service "$SERVICE_NAME" restart ;;
    *) return 1 ;;
  esac
}

service_stop_disable() {
  local init
  init=$(init_type)
  case "$init" in
    systemd) systemctl disable --now "$SERVICE_NAME" 2>/dev/null || true ;;
    openrc) rc-service "$SERVICE_NAME" stop 2>/dev/null || true; rc-update del "$SERVICE_NAME" default 2>/dev/null || true ;;
  esac
}

service_restart() {
  case "$(init_type)" in
    systemd) systemctl restart "$SERVICE_NAME" ;;
    openrc) rc-service "$SERVICE_NAME" restart ;;
    *) return 1 ;;
  esac
}

service_state() {
  case "$(init_type)" in
    systemd) systemctl is-active "$SERVICE_NAME" 2>/dev/null || true ;;
    openrc)
      if rc-service "$SERVICE_NAME" status >/dev/null 2>&1; then
        printf 'active\n'
      else
        printf 'inactive\n'
      fi
      ;;
    *) printf '未知 init 系统\n' ;;
  esac
}

metrics_connections() {
  command_exists curl || return 0
  curl -fsS --max-time 3 "http://${METRICS_ADDR}/metrics" 2>/dev/null |
    awk '/^cloudflared_tunnel_ha_connections[[:space:]]/{sum += $2} END {if (NR) printf "%s", sum + 0}'
}

current_run_has_connection() {
  [[ -r "$LOG_FILE" ]] || return 1
  awk '
    /^===== yjl-argo start / { seen_start = 1; registered = 0 }
    seen_start && /Registered tunnel connection/ { registered = 1 }
    END { exit registered ? 0 : 1 }
  ' "$LOG_FILE"
}

cloudflare_dns_hint() {
  command_exists getent || return 0
  local edge_ip
  edge_ip=$(getent ahostsv4 region1.v2.argotunnel.com 2>/dev/null | awk 'NR == 1 { print $1 }')
  if [[ "$edge_ip" == 198.18.* ]]; then
    warn "Cloudflare 边缘域名被解析到 fake-IP ${edge_ip}，连接器无法建立 TLS。请改用真实 DNS 或修正到真实网关的路由。"
  fi
}

discover_quick_url() {
  [[ -r "$LOG_FILE" ]] || return 0
  grep -Eo 'https://[a-z0-9-]+\.trycloudflare\.com' "$LOG_FILE" 2>/dev/null |
    grep -v '^https://api\.trycloudflare\.com$' | tail -n 1 || true
}

target_check() {
  [[ -n "$TARGET" ]] || return 1
  command_exists curl || return 0
  if curl -kIsS --max-time 4 --connect-timeout 2 "$TARGET" >/dev/null 2>&1; then
    ok "本地目标可连通：${TARGET}"
  else
    warn "无法以 HTTP HEAD 探测本地目标：${TARGET}。WS 服务可能不接受 HEAD；隧道仍会启动，请用真实客户端验证。"
  fi
}

hostname_check() {
  [[ -n "$PUBLIC_HOSTNAME" ]] || return 0
  command_exists curl || return 0
  local url status_code
  url="https://${PUBLIC_HOSTNAME}${PUBLIC_PATH:-/}"
  status_code=$(curl -kIsS --max-time 8 --connect-timeout 4 -o /dev/null -w '%{http_code}' "$url" 2>/dev/null || true)
  if [[ "$status_code" =~ ^[234][0-9][0-9]$ ]]; then
    ok "自有域名 HTTPS 回应：${url} (HTTP ${status_code})"
  elif [[ "$status_code" =~ ^5[0-9][0-9]$ ]]; then
    err "自有域名请求失败：${url} (HTTP ${status_code})。Cloudflare 已响应，但 Tunnel 或本地目标未成功回源。"
  else
    warn "未能通过 HTTPS 访问自有域名：${url}（HTTP ${status_code:-000}）。请确认 Dashboard Public Hostname、DNS 和本机目标服务。"
  fi
}

status() {
  load_config
  line
  say "${C_BLUE}${APP_NAME} 状态${C_RESET}"
  say "系统服务：$(service_state)"
  local process_count
  process_count=$( (ps -eo args 2>/dev/null || ps) | awk -v metrics="$METRICS_ADDR" '/\/cloudflared/ && index($0, "--metrics " metrics) { count++ } END { print count + 0 }' )
  say "运行进程：${process_count:-0} 个 cloudflared"
  say "隧道模式：${MODE:-未配置}"
  say "本地目标：${TARGET:-未配置}"
  say "连接协议：${PROTOCOL:-http2}"
  cloudflare_dns_hint
  local connections url
  connections=$(metrics_connections || true)
  if current_run_has_connection; then
    ok 'Cloudflare Tunnel 已注册（当前服务运行日志已确认）。'
  elif [[ -n "$connections" && "$connections" != 0 ]]; then
    warn "cloudflared 配置 HA 连接数：${connections}；当前运行日志尚未确认已注册。"
  elif service_exists; then
    warn '暂未读取到 metrics 连接数。服务可能刚启动，或尚未连上 Cloudflare。'
  fi
  if [[ "$MODE" == 'quick' ]]; then
    url=$(discover_quick_url)
    [[ -n "$url" ]] && say "临时域名：${url}" || warn '日志中还没有发现 trycloudflare 临时域名。'
  elif [[ "$MODE" == 'token' ]]; then
    say '命名隧道：Token 已保存（不会在菜单中显示）。'
    if [[ -n "$PUBLIC_HOSTNAME" ]]; then
      say "自有域名：https://${PUBLIC_HOSTNAME}${PUBLIC_PATH:-/}"
      hostname_check
    else
      warn '未记录自有域名。若要公网访问，请在 Cloudflare Dashboard 配置 Public Hostname。'
    fi
  fi
  target_check || true
  line
}

run_tunnel() {
  load_config
  [[ -x "$CLOUDFLARED" ]] || { err "找不到 cloudflared：${CLOUDFLARED}"; exit 1; }
  [[ -n "$MODE" && -n "$TARGET" ]] || { err '未完成隧道配置。请执行 yjl 后选择“创建或修改隧道”。'; exit 1; }
  mkdir -p "$(dirname "$LOG_FILE")"
  touch "$LOG_FILE"
  chmod 600 "$LOG_FILE" 2>/dev/null || true
  printf '\n===== yjl-argo start mode=%s at=%s =====\n' "$MODE" "$(date -u +%FT%TZ)" >> "$LOG_FILE"
  local common=(--no-autoupdate --protocol "${PROTOCOL:-http2}" --metrics "$METRICS_ADDR")
  if [[ "$MODE" == 'quick' ]]; then
    exec "$CLOUDFLARED" "${common[@]}" tunnel --url "$TARGET" >> "$LOG_FILE" 2>&1
  elif [[ "$MODE" == 'token' && -n "$TOKEN" ]]; then
    exec "$CLOUDFLARED" "${common[@]}" tunnel --url "$TARGET" run --token "$TOKEN" >> "$LOG_FILE" 2>&1
  else
    err '配置无效。'; exit 1
  fi
}

configure_tunnel() {
  require_root || return
  ensure_cloudflared || return
  load_config
  line
  say '创建 / 修改 Argo 隧道'
  say '本工具只转发流量，不安装 sing-box。目标必须是已经运行的本地 HTTP 或 WebSocket 服务。'
  say '例：http://127.0.0.1:8080  或  http://[::1]:8080'
  read -r -p "本地目标地址 [${TARGET:-http://127.0.0.1:8080}]：" new_target
  new_target=${new_target:-${TARGET:-http://127.0.0.1:8080}}
  valid_target "$new_target" || { err '目标地址只能是 http:// 或 https://，且不能含空格。'; return 1; }
  say '选择隧道类型：'
  say '  1. 临时隧道 TryCloudflare（无需账号，域名重启后会变化，不适合长期使用）'
  say '  2. 命名隧道 Token（在 Cloudflare Zero Trust 创建 Tunnel 后粘贴 Token，适合长期使用）'
  local default_choice='1'
  [[ "$MODE" == 'token' ]] && default_choice='2'
  read -r -p "请选择 [${default_choice}]：" selected
  case "${selected:-$default_choice}" in
    1) MODE='quick'; TOKEN=''; PUBLIC_HOSTNAME=''; PUBLIC_PATH='' ;;
    2)
      MODE='token'
      say '在 Cloudflare Zero Trust -> Networks -> Tunnels -> Create a tunnel -> Cloudflared 中复制 Token。'
      read -r -s -p '粘贴 Tunnel Token：' new_token; printf '\n'
      new_token=${new_token:-$TOKEN}
      [[ -n "$new_token" ]] || { err 'Token 不能为空。'; return 1; }
      TOKEN=$new_token
      say '在 Cloudflare Dashboard -> Tunnel -> Public Hostname 中已创建的完整域名，例如 test.example.com。'
      read -r -p "自有完整域名 [${PUBLIC_HOSTNAME:-留空}]：" new_hostname
      new_hostname=${new_hostname:-$PUBLIC_HOSTNAME}
      if [[ -n "$new_hostname" ]]; then
        valid_hostname "$new_hostname" || { err '域名格式无效，请只填写完整域名，不要填写 https:// 或路径。'; return 1; }
      fi
      PUBLIC_HOSTNAME=$new_hostname
      read -r -p "Dashboard 路径匹配 [${PUBLIC_PATH:-留空，匹配所有路径}]：" new_path
      new_path=${new_path:-$PUBLIC_PATH}
      valid_public_path "$new_path" || { err '路径必须以 / 开头，或留空；不要包含空格。'; return 1; }
      PUBLIC_PATH=$new_path
      ;;
    *) err '无效选择。'; return 1 ;;
  esac
  TARGET=$new_target
  PROTOCOL='http2'
  QUICK_URL=''
  write_config
  write_service
  target_check || true
  service_enable_start
  sleep 3
  status
  if [[ "$MODE" == quick ]]; then
    local url
    url=$(discover_quick_url)
    [[ -n "$url" ]] && ok "临时隧道已创建：${url}" || warn '隧道正在建立，请稍后从“查看状态”或“查看日志”读取临时域名。'
  else
    ok '命名隧道已启动。请在 Cloudflare Dashboard 的 Public Hostname / 路由规则中将服务地址设为上方“本地目标”；Dashboard 的远端规则会优先于 --url。'
  fi
}

restart_tunnel() {
  require_root || return
  service_exists || { err '尚未创建 yjl-argo 服务。'; return 1; }
  service_restart
  sleep 2
  status
}

show_log() {
  if [[ -r "$LOG_FILE" ]]; then
    tail -n 80 "$LOG_FILE"
  else
    warn "还没有日志文件：${LOG_FILE}"
  fi
}

uninstall_local() {
  require_root || return
  read -r -p '只卸载本机 yjl-argo 服务和本地 Token/配置；不会删除 Cloudflare Dashboard 的命名隧道。确认 [y/N]：' answer
  [[ "$answer" =~ ^[Yy]$ ]] || return 0
  service_stop_disable
  rm -f "$SYSTEMD_UNIT" "$OPENRC_UNIT"
  [[ "$(init_type)" == systemd ]] && systemctl daemon-reload
  rm -rf "$APP_DIR"
  rm -f "$LOG_FILE"
  ok '本机 Argo 服务与配置已移除。cloudflared 二进制和 Cloudflare Dashboard 中的 Tunnel 保留。'
}

update_cloudflared() {
  require_root || return
  service_stop_disable
  rm -f "$CLOUDFLARED"
  install_cloudflared
  service_exists && service_enable_start || true
}

menu() {
  while true; do
    clear 2>/dev/null || true
    line
    say "${C_BLUE}${APP_NAME} - 独立 Argo 隧道管理器${C_RESET}"
    say '不安装、不管理 sing-box；仅管理 cloudflared 到本地 HTTP/WebSocket 服务的转发。'
    line
    status
    say '  1. 创建 / 修改 Argo 隧道'
    say '  2. 查看 Argo 状态'
    say '  3. 重启 Argo 隧道'
    say '  4. 查看最近日志'
    say '  5. 安装 / 更新 cloudflared'
    say '  6. 卸载本机 Argo 服务与配置'
    say '  0. 退出'
    read -r -p '请输入选项：' choice
    case "$choice" in
      1) configure_tunnel; pause ;;
      2) status; pause ;;
      3) restart_tunnel; pause ;;
      4) show_log; pause ;;
      5) update_cloudflared; pause ;;
      6) uninstall_local; pause ;;
      0) exit 0 ;;
      *) warn '请输入 0-6。'; sleep 1 ;;
    esac
  done
}

case "${1:-menu}" in
  menu) menu ;;
  run) run_tunnel ;;
  status) status ;;
  install-cloudflared) install_cloudflared ;;
  *) err "未知参数：$1"; exit 2 ;;
esac
