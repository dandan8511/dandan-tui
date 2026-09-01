#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
TMP_DIR=$(mktemp -d)
trap 'rm -rf "$TMP_DIR"' EXIT

assert_contains() {
  local needle=$1 file=$2
  grep -F -- "$needle" "$file" >/dev/null || {
    printf 'missing expected text: %s\n' "$needle" >&2
    exit 1
  }
}

cp "$ROOT_DIR/tests/fake-cloudflared.sh" "$TMP_DIR/cloudflared"
chmod 755 "$TMP_DIR/cloudflared"

for mode in quick token; do
  config_dir="$TMP_DIR/$mode"
  mkdir -p "$config_dir"
  cat > "$config_dir/config.env" <<EOF
MODE=$mode
TARGET=http://127.0.0.1:18080
TOKEN=example-token
PROTOCOL=http2
QUICK_URL=
PUBLIC_HOSTNAME=test.example.com
PUBLIC_PATH=/unit
EOF
  args_file="$TMP_DIR/$mode.args"
  log_file="$TMP_DIR/$mode.log"
  YJL_ARGO_CONFIG_DIR="$config_dir" \
  YJL_ARGO_LOG_FILE="$log_file" \
  YJL_ARGO_CLOUDFLARED="$TMP_DIR/cloudflared" \
  YJL_FAKE_ARGS="$args_file" \
  "$ROOT_DIR/yjl-argo.sh" run
  assert_contains '--no-autoupdate' "$args_file"
  assert_contains '--protocol http2' "$args_file"
  assert_contains '--metrics 127.0.0.1:20241' "$args_file"
  assert_contains 'tunnel --url http://127.0.0.1:18080' "$args_file"
  assert_contains '===== yjl-argo start mode=' "$log_file"
  assert_contains 'unit-test.trycloudflare.com' "$log_file"
done
assert_contains 'run --token example-token' "$TMP_DIR/token.args"
assert_contains 'supervisor="supervise-daemon"' "$ROOT_DIR/yjl-argo.sh"
assert_contains 'respawn_delay=5' "$ROOT_DIR/yjl-argo.sh"
assert_contains 'systemctl restart "$SERVICE_NAME"' "$ROOT_DIR/yjl-argo.sh"
assert_contains 'elif [[ "$status_code" =~ ^5[0-9][0-9]$ ]]' "$ROOT_DIR/yjl-argo.sh"
assert_contains 'Cloudflare 边缘域名被解析到 fake-IP' "$ROOT_DIR/yjl-argo.sh"
assert_contains 'ps -eo args' "$ROOT_DIR/yjl-argo.sh"
assert_contains "grep -v '^https://api" "$ROOT_DIR/yjl-argo.sh"

if [[ ${EUID} -eq 0 ]]; then
  YJL_ARGO_INSTALL_DIR="$TMP_DIR/install/lib" \
  YJL_ARGO_COMMAND_PATH="$TMP_DIR/install/bin/yjl" \
sh "$ROOT_DIR/install.sh" >/dev/null
  [[ -L "$TMP_DIR/install/bin/yjl" ]]
  [[ -x "$TMP_DIR/install/lib/yjl-argo.sh" ]]
else
  printf 'Installer integration test skipped: root is required by design.\n'
fi

printf 'All YJL Argo tests passed.\n'
