#!/usr/bin/env bash
# Project-owned TCP Brutal installer and persistent-rule manager.
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "$0")" && pwd)"
VENDORED_DIR="${SCRIPT_DIR}/tcp-brutal"
STATE_DIR="${TCP_BRUTAL_STATE_DIR:-/etc/tcp-brutal}"
RULES_FILE="${STATE_DIR}/rules.conf"
RESTORE_SCRIPT="${TCP_BRUTAL_RESTORE_SCRIPT:-/usr/local/libexec/tcp-brutal-restore-rules}"
SYSTEMD_UNIT="${TCP_BRUTAL_SYSTEMD_UNIT:-/etc/systemd/system/tcp-brutal-rules.service}"
OPENRC_SERVICE="${TCP_BRUTAL_OPENRC_SERVICE:-/etc/init.d/tcp-brutal-rules}"
BRUTALCTL="${TCP_BRUTAL_BRUTALCTL:-/usr/local/bin/brutalctl}"
VENDORED_TARBALL_SHA256="fbf0fd979102c7aff5d7b91c2d9e12fb1a00aa8521c83a825f4b66fb21daf236"

die() {
    printf '错误：%s\n' "$*" >&2
    exit 1
}

note() {
    printf '%s\n' "$*"
}

require_root() {
    [ "${EUID}" -eq 0 ] || die "需要 root 权限。请在 TUI 中以 root 运行，或使用 sudo。"
}

is_ipv4() {
    local value="$1" octet
    [[ "$value" =~ ^[0-9]{1,3}(\.[0-9]{1,3}){3}$ ]] || return 1
    local IFS=.
    read -r -a octet <<< "$value"
    for value in "${octet[@]}"; do
        ((10#$value <= 255)) || return 1
    done
}

normalize_prefix() {
    local value="$1" address prefix
    if [[ "$value" == */* ]]; then
        address="${value%/*}"
        prefix="${value#*/}"
    else
        address="$value"
        prefix=32
    fi
    is_ipv4 "$address" || return 1
    [[ "$prefix" =~ ^[0-9]{1,2}$ ]] && ((10#$prefix <= 32)) || return 1
    printf '%s/%s\n' "$address" "$prefix"
}

is_rate() {
    [[ "$1" =~ ^[1-9][0-9]{0,6}$ ]]
}

show_local_addresses() {
    local addresses
    addresses="$(ip -o -4 addr show scope global 2>/dev/null | awk '{print $4}' || true)"
    if [ -n "$addresses" ]; then
        note "本机全局 IPv4："
        printf '  %s\n' $addresses
    else
        note "未从 ip 命令发现全局 IPv4；请按实际客户端或对端地址填写目标。"
    fi
}

show_route_hint() {
    local prefix="$1" address="${1%/*}"
    note "当前到 ${prefix} 的路由（brutalctl 将复制其下一跳，并加 proto 233）："
    ip -4 route get "$address" 2>/dev/null || true
}

has_usable_target_route() {
    local address="$1" route
    route="$(ip -4 route get "$address" 2>/dev/null)" || return 1
    [[ "$route" != local\ * ]]
}

detect_ssh_peer() {
    local peer="${SSH_CONNECTION%% *}"
    is_ipv4 "$peer" && printf '%s\n' "$peer"
}

detect_active_tcp_peers() {
    local line peer address
    while IFS= read -r line; do
        [[ "$line" == *'users:(("sshd"'* ]] && continue
        peer="$(awk '{print $5}' <<< "$line")"
        address="${peer%:*}"
        is_ipv4 "$address" || continue
        has_usable_target_route "$address" || continue
        printf '%s\n' "$address"
    done < <(ss -Htnp state established 2>/dev/null || true) | sort -u
}

init_system() {
    local distro_id=""
    if [ -r /etc/os-release ]; then
        distro_id="$(. /etc/os-release; printf '%s' "${ID:-}")"
    fi
    if [ "$distro_id" = "alpine" ] || { command -v rc-service >/dev/null 2>&1 && [ ! -d /run/systemd/system ]; }; then
        printf 'openrc\n'
    elif command -v systemctl >/dev/null 2>&1; then
        printf 'systemd\n'
    else
        return 1
    fi
}

prepare_alpine_build_dependencies() {
    local distro_id=""
    if [ -r /etc/os-release ]; then
        distro_id="$(. /etc/os-release; printf '%s' "${ID:-}")"
    fi
    [ "$distro_id" = "alpine" ] || return 0
    command -v apk >/dev/null 2>&1 || die "Alpine 未找到 apk，无法安装 TCP Brutal 的构建依赖。"
    note "检测到 Alpine：确保 DKMS、当前内核头文件和 build-base 可用。"
    apk add --no-cache bash build-base dkms linux-headers curl grep
}

write_restore_script() {
    mkdir -p -- "$(dirname -- "$RESTORE_SCRIPT")" "$STATE_DIR"
    cat > "$RESTORE_SCRIPT" <<'EOF'
#!/usr/bin/env bash
set -Eeuo pipefail
RULES_FILE="/etc/tcp-brutal/rules.conf"
BRUTALCTL="/usr/local/bin/brutalctl"

[ -x "$BRUTALCTL" ] || { printf '%s\n' "tcp-brutal: brutalctl is unavailable" >&2; exit 1; }
[ -f "$RULES_FILE" ] || exit 0

while read -r prefix rate extra; do
    [ -n "${prefix:-}" ] || continue
    [[ "$prefix" =~ ^[0-9]{1,3}(\.[0-9]{1,3}){3}/([0-9]|[12][0-9]|3[0-2])$ ]] || {
        printf 'tcp-brutal: ignore invalid prefix %s\n' "$prefix" >&2
        continue
    }
    [[ "$rate" =~ ^[1-9][0-9]{0,6}$ ]] || {
        printf 'tcp-brutal: ignore invalid rate for %s\n' "$prefix" >&2
        continue
    }
    "$BRUTALCTL" add "$prefix" "$rate"
done < "$RULES_FILE"
EOF
    chmod 700 "$RESTORE_SCRIPT"
}

install_systemd_service() {
    cat > "$SYSTEMD_UNIT" <<EOF
[Unit]
Description=Restore TCP Brutal destination rules
Wants=network-online.target
After=network-online.target

[Service]
Type=oneshot
ExecStart=${RESTORE_SCRIPT}
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
EOF
    chmod 644 "$SYSTEMD_UNIT"
    systemctl daemon-reload
    systemctl enable --now tcp-brutal-rules.service
}

install_openrc_service() {
    cat > "$OPENRC_SERVICE" <<EOF
#!/sbin/openrc-run
description="Restore TCP Brutal destination rules"

depend() {
    need net
    after firewall
}

start() {
    ebegin "Restoring TCP Brutal destination rules"
    ${RESTORE_SCRIPT}
    eend \$?
}
EOF
    chmod 755 "$OPENRC_SERVICE"
    rc-update add tcp-brutal-rules default
    rc-service tcp-brutal-rules restart
}

install_persistence() {
    local init
    init="$(init_system)" || die "未识别 systemd 或 OpenRC，规则已保存到 ${RULES_FILE}，请手动在开机时执行 ${RESTORE_SCRIPT}。"
    write_restore_script
    case "$init" in
        systemd) install_systemd_service ;;
        openrc) install_openrc_service ;;
    esac
    note "已启用开机恢复：${RULES_FILE}"
}

save_rule() {
    local prefix="$1" rate="$2" temporary
    mkdir -p -- "$STATE_DIR"
    temporary="$(mktemp "${STATE_DIR}/.rules.XXXXXX")"
    if [ -f "$RULES_FILE" ]; then
        awk -v prefix="$prefix" '$1 != prefix { print }' "$RULES_FILE" > "$temporary"
    fi
    printf '%s %s\n' "$prefix" "$rate" >> "$temporary"
    chmod 600 "$temporary"
    mv -f -- "$temporary" "$RULES_FILE"
}

capture_live_rules() {
    local prefix rate route normalized
    [ -x "$BRUTALCTL" ] || return 0
    while read -r prefix rate route; do
        normalized="$(normalize_prefix "$prefix" 2>/dev/null || true)"
        rate="${rate%%.*}"
        if [ "$route" != "yes" ]; then
            [ -n "$normalized" ] && note "跳过没有 brutal 路由的旧规则：${normalized}"
        elif [ -n "$normalized" ] && is_rate "$rate"; then
            save_rule "$normalized" "$rate"
            note "保留现有 TCP Brutal 规则：${normalized} ${rate} Mbps"
        fi
    done < <("$BRUTALCTL" list 2>/dev/null | awk 'NR > 1 {print $1, $2, $6}')
}

ensure_persistence_if_saved_rules() {
    [ -s "$RULES_FILE" ] || return 0
    install_persistence
}

add_rule() {
    local prefix rate
    prefix="$(normalize_prefix "$1")" || die "目标必须是 IPv4 或 IPv4/CIDR，例如 188.165.226.219/32。"
    rate="$2"
    is_rate "$rate" || die "速率必须是 1 到 9999999 的整数 Mbps。"
    [ -x "$BRUTALCTL" ] || die "未找到 ${BRUTALCTL}；请先完成安装并确认 brutal 模块可以加载。"
    has_usable_target_route "${prefix%/*}" || die "${prefix} 是本机地址或当前没有可用的 IPv4 出站路由，不能作为 TCP Brutal 目标。"
    show_route_hint "$prefix"
    "$BRUTALCTL" add "$prefix" "$rate"
    save_rule "$prefix" "$rate"
    install_persistence
    note "已添加并持久化：brutalctl add ${prefix} ${rate}"
}

list_rules() {
    show_local_addresses
    [ -f "$RULES_FILE" ] && { note "已保存、将在重启后恢复的规则："; sed 's/^/  /' "$RULES_FILE"; } || note "尚未保存持久化规则。"
    if [ -x "$BRUTALCTL" ]; then
        note "当前已加载的 TCP Brutal 规则："
        "$BRUTALCTL" list || true
        note "当前由 brutalctl 创建的路由："
        ip -4 route show proto 233 || true
    fi
}

remove_rule() {
    local prefix temporary
    prefix="$(normalize_prefix "$1")" || die "目标必须是 IPv4 或 IPv4/CIDR。"
    [ -x "$BRUTALCTL" ] || die "未找到 ${BRUTALCTL}。"
    "$BRUTALCTL" del "$prefix"
    if [ -f "$RULES_FILE" ]; then
        temporary="$(mktemp "${STATE_DIR}/.rules.XXXXXX")"
        awk -v prefix="$prefix" '$1 != prefix { print }' "$RULES_FILE" > "$temporary"
        chmod 600 "$temporary"
        mv -f -- "$temporary" "$RULES_FILE"
    fi
    note "已删除 ${prefix} 的即时和持久化规则。"
}

configure_routes() {
    local target rate answer choice ssh_peer
    local -a candidates
    note
    note "TCP Brutal 按目标地址匹配发送方向的新 TCP 连接。"
    note "服务器通常应填写客户端/对端公网 IP，不是服务器自己的 IP；下面列出本机地址仅供核对。"
    show_local_addresses

    ssh_peer="$(detect_ssh_peer || true)"
    if [ -n "$ssh_peer" ] && has_usable_target_route "$ssh_peer"; then
        note "已从 SSH_CONNECTION 自动识别当前管理客户端：${ssh_peer}，按默认 1000 Mbps 添加。"
        add_rule "${ssh_peer}/32" 1000
        return
    fi

    mapfile -t candidates < <(detect_active_tcp_peers)
    if [ "${#candidates[@]}" -eq 1 ]; then
        note "已从当前非 SSH TCP 连接自动识别唯一对端：${candidates[0]}，按默认 1000 Mbps 添加。"
        add_rule "${candidates[0]}/32" 1000
        return
    fi
    if [ "${#candidates[@]}" -gt 1 ]; then
        note "检测到多个当前 TCP 对端，不能安全猜测实际客户端。"
        for choice in "${!candidates[@]}"; do
            printf '  %d. %s\n' "$((choice + 1))" "${candidates[$choice]}"
        done
        read -r -p "选择要应用的对端编号（直接回车跳过）: " choice
        if [[ "$choice" =~ ^[0-9]+$ ]] && ((choice >= 1 && choice <= ${#candidates[@]})); then
            target="${candidates[$((choice - 1))]}"
            read -r -p "总速率 Mbps [1000]: " rate
            add_rule "${target}/32" "${rate:-1000}"
        fi
        return
    fi

    note "未检测到可安全自动使用的远端地址。请让客户端先建立 TCP 连接后重试，或手工填写一次。"
    while true; do
        read -r -p "现在添加目标 IPv4/CIDR（直接回车跳过）: " target
        [ -n "$target" ] || break
        read -r -p "总速率 Mbps [1000]: " rate
        rate="${rate:-1000}"
        add_rule "$target" "$rate"
        read -r -p "继续添加规则？[y/N]: " answer
        [[ "$answer" =~ ^[Yy]$ ]] || break
    done
}

run_online_install() {
    local installer
    prepare_alpine_build_dependencies
    capture_live_rules
    installer="$(mktemp /tmp/tcp-brutal-online.XXXXXX)"
    trap 'rm -f -- "$installer"' RETURN
    note "从 https://tcp.hy2.sh 下载并执行当前官方安装器。"
    curl -fsSL --retry 3 --connect-timeout 15 --max-time 180 https://tcp.hy2.sh/ -o "$installer"
    bash -n "$installer"
    bash "$installer"
    ensure_persistence_if_saved_rules
    configure_routes
}

run_offline_install() {
    local tarball="${VENDORED_DIR}/dkms.tar.gz" actual_hash
    prepare_alpine_build_dependencies
    capture_live_rules
    [ -f "$tarball" ] || die "缺少离线 DKMS 包：${tarball}"
    actual_hash="$(sha256sum "$tarball" | awk '{print $1}')"
    [ "$actual_hash" = "$VENDORED_TARBALL_SHA256" ] || die "离线 DKMS 包校验失败，拒绝执行。"
    # Do not use grep -q here: under pipefail it may close early and make tar fail with SIGPIPE.
    tar -tzf "$tarball" | grep -x './dkms_source_tree/dkms.conf' > /dev/null || die "离线 DKMS 包结构异常。"
    bash -n "${VENDORED_DIR}/scripts/install_dkms.sh"
    note "执行仓库内 tcp-brutal 完整快照；不会下载上游 GitHub 的 DKMS 源码包。"
    bash "${VENDORED_DIR}/scripts/install_dkms.sh" install --local "$tarball"
    ensure_persistence_if_saved_rules
    configure_routes
}

usage() {
    cat <<'EOF'
用法：
  tcp-brutal-manager.sh online              在线执行 https://tcp.hy2.sh 官方安装器
  tcp-brutal-manager.sh offline             使用本仓库 tcp-brutal 完整快照安装
  tcp-brutal-manager.sh add IP[/CIDR] Mbps  添加即时规则并设置开机恢复
  tcp-brutal-manager.sh del IP[/CIDR]       删除即时及持久化规则
  tcp-brutal-manager.sh list                查看本机地址、规则与 proto 233 路由
EOF
}

main() {
    local command="${1:-}"
    case "$command" in
        online) require_root; run_online_install ;;
        offline) require_root; run_offline_install ;;
        add) require_root; [ "$#" -eq 3 ] || die "用法：add IP[/CIDR] Mbps"; add_rule "$2" "$3" ;;
        del) require_root; [ "$#" -eq 2 ] || die "用法：del IP[/CIDR]"; remove_rule "$2" ;;
        list) list_rules ;;
        help|--help|-h|'') usage ;;
        *) die "未知命令：${command}" ;;
    esac
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    main "$@"
fi
