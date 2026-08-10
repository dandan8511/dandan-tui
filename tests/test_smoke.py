import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import tui


class ConfigSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = json.loads((ROOT / "scripts.json").read_text(encoding="utf-8"))

    def test_config_shape_and_unique_action_ids(self):
        self.assertIsInstance(self.config["categories"], list)
        self.assertIsInstance(self.config["actions"], list)
        action_ids = [action["id"] for action in self.config["actions"]]
        self.assertEqual(len(action_ids), len(set(action_ids)))
        self.assertGreaterEqual(len(action_ids), 82)

    def test_fixed_online_endpoints(self):
        actions = {action["id"]: action for action in self.config["actions"]}
        self.assertEqual(actions["warp"]["url"], "https://gitlab.com/fscarmen/warp/-/raw/main/menu.sh")
        self.assertEqual(
            actions["onepanel"]["url"],
            "https://resource.fit2cloud.com/1panel/package/v2/quick_start.sh",
        )

    def test_each_action_has_dispatch_or_supported_kind(self):
        source = (ROOT / "tui.py").read_text(encoding="utf-8")
        special_ids = {
            "system_info", "apt_upgrade", "log_manage", "kernel_manage", "ssl_manage",
            "network_manage", "grub_manage", "ip_preference", "webdav_manage", "ssh_config",
            "tcp_status", "tcp_remove_all", "docker_status", "docker_containers", "docker_images",
            "docker_logs", "docker_start", "docker_stop", "docker_restart", "docker_exec",
            "docker_pull", "docker_remove", "docker_compose", "docker_prune",
            "docker_daemon_restart", "lazydocker", "custom_script",
        }
        supported_kinds = {"online", "local_script", "tcp_online", "exit"}
        missing = []
        for action in self.config["actions"]:
            if (
                action.get("kind") in supported_kinds
                or action.get("tcp_profile")
                or action["id"] in special_ids
            ):
                continue
            if action["id"] not in source:
                missing.append(action["id"])
        self.assertEqual(missing, [])

    def test_tcp_brutal_is_first_local_action(self):
        actions = [action for action in self.config["actions"] if action.get("category") == "tcp_tuning"]
        self.assertEqual(actions[0]["id"], "tcp_brutal_install")
        self.assertEqual(actions[0]["kind"], "local_script")
        self.assertEqual(actions[0]["path"], "scripts/install-tcp-brutal.sh")
        self.assertTrue((ROOT / actions[0]["path"]).is_file())
        for protocol in ("ShadowTLS", "Shadowsocks", "Trojan", "VMess + WS", "VLESS + WS + TLS", "H2 + Reality", "gRPC + Reality"):
            self.assertIn(protocol, actions[0]["description"])

    def test_nodeseek_menu_actions(self):
        categories = {category["id"]: category["title"] for category in self.config["categories"]}
        self.assertEqual(categories.get("nodeseek"), "Nodeseek论坛")
        actions = [action for action in self.config["actions"] if action.get("category") == "nodeseek"]
        self.assertEqual([action["id"] for action in actions], [
            "nodeseek_bbr", "nodeseek_tcp_multifunction", "nodeseek_tcpx", "nodeseek_window_tuning",
        ])
        self.assertEqual(actions[0]["tcp_profile"], "nodeseek-bbr")
        self.assertTrue(all(action["needs_root"] for action in actions))
        window_tuning = actions[-1]
        self.assertEqual(window_tuning["kind"], "local_script")
        self.assertEqual(window_tuning["path"], "scripts/nekoneko-tools.sh")
        self.assertTrue((ROOT / window_tuning["path"]).is_file())

    def test_tcp_brutal_repair_only_targets_supported_nodes(self):
        jsonc = '''{
  "inbounds": [{
    "type": "vless",
    "tag": "jp vless-ws-tls",
    "transport": {"type": "ws"},
    "multiplex": {"enabled": false}
  }]
}'''
        patched, changed = tui.TUI._tcp_brutal_patch_jsonc_inbound(jsonc)
        self.assertTrue(changed)
        self.assertIn('"enabled": true', patched)
        self.assertIn('"brutal": {', patched)

        singbox = {
            "outbounds": [
                {"type": "vless", "tag": "jp vless-ws-tls", "transport": {"type": "ws"}, "multiplex": {"enabled": False}},
                {"type": "vless", "tag": "jp xtls-reality", "flow": "xtls-rprx-vision", "multiplex": {"enabled": False}},
                {"type": "vless", "tag": "jp h2-reality", "transport": {"type": "http"}},
            ]
        }
        patched, changed = tui.TUI._tcp_brutal_patch_singbox_subscription(
            json.dumps(singbox), {"jp vless-ws-tls", "jp xtls-reality", "jp h2-reality"}
        )
        self.assertTrue(changed)
        repaired = json.loads(patched)
        self.assertTrue(repaired["outbounds"][0]["multiplex"]["brutal"]["enabled"])
        self.assertFalse(repaired["outbounds"][1]["multiplex"]["enabled"])
        self.assertTrue(repaired["outbounds"][2]["multiplex"]["brutal"]["enabled"])

        yaml = '''- name: jp vless-ws-tls
  type: vless
  network: ws
  smux:
    enabled: false
  brutal-opts:
    enabled: false
- name: jp xtls-reality
  type: vless
  network: tcp
  flow: xtls-rprx-vision
  smux:
    enabled: false
  brutal-opts:
    enabled: false
'''
        patched, changed = tui.TUI._tcp_brutal_patch_yaml_subscription(yaml, {"jp vless-ws-tls", "jp xtls-reality"})
        self.assertTrue(changed)
        self.assertIn("jp vless-ws-tls\n  type: vless\n  network: ws\n  smux:\n    enabled: true", patched)
        self.assertIn("jp xtls-reality\n  type: vless\n  network: tcp\n  flow: xtls-rprx-vision\n  smux:\n    enabled: false", patched)

        missing = '''proxies:
  - name: jp vless-ws-tls
    type: vless
    network: ws
    smux:
      enabled: true
rules:
  - DOMAIN-SUFFIX,example.com,DIRECT
'''
        patched, changed = tui.TUI._tcp_brutal_patch_yaml_subscription(missing, {"jp vless-ws-tls"})
        self.assertTrue(changed)
        self.assertIn("    brutal-opts:\n      enabled: true", patched)
        self.assertIn("rules:\n  - DOMAIN-SUFFIX,example.com,DIRECT", patched)


class LocalBehaviorTests(unittest.TestCase):
    def test_shell_and_python_syntax(self):
        subprocess.run(["bash", "-n", "launch.sh"], cwd=ROOT, check=True)
        subprocess.run(["bash", "-n", "run.sh"], cwd=ROOT, check=True)
        subprocess.run(["bash", "-n", "scripts/install-tcp-brutal.sh"], cwd=ROOT, check=True)
        subprocess.run(["bash", "-n", "scripts/nekoneko-tools.sh"], cwd=ROOT, check=True)
        subprocess.run([sys.executable, "-m", "py_compile", "tui.py"], cwd=ROOT, check=True)

    def test_noninteractive_commands(self):
        env = {
            **__import__("os").environ,
            "YJL_TUI_CACHE_DIR": tempfile.mkdtemp(),
            "YJL_TUI_LOG_DIR": tempfile.mkdtemp(),
            "YJL_TUI_STATE_DIR": tempfile.mkdtemp(),
        }
        check = subprocess.run(["./run.sh", "--check"], cwd=ROOT, env=env, capture_output=True, text=True)
        listing = subprocess.run(["./run.sh", "--list"], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(check.returncode, 0)
        self.assertIn("配置 OK", check.stdout)
        self.assertEqual(listing.returncode, 0)
        self.assertIn("warp", listing.stdout)
        self.assertIn("onepanel", listing.stdout)

    def test_input_and_name_validation(self):
        self.assertTrue(tui.TUI.docker_name("nginx:latest", image=True))
        self.assertFalse(tui.TUI.docker_name("bad name"))
        self.assertEqual(tui.safe_name("中文 action"), "action")
        self.assertIsNotNone(tui.TUI.builtin("domain_latency"))


if __name__ == "__main__":
    unittest.main()
