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

    def test_tcpfit_is_vendored_tcp_menu_entry(self):
        actions = {action["id"]: action for action in self.config["actions"]}
        self.assertNotIn("tcp_exit", actions)
        action = actions["tcpfit"]
        self.assertEqual(action["category"], "tcp_tuning")
        self.assertEqual(action["kind"], "local_script")
        self.assertEqual(action["path"], "scripts/tcpfit/tcpfit.sh")
        self.assertEqual(action["interpreter"], "bash")
        self.assertTrue(action["needs_root"])
        self.assertIn("99.", action["title"])

        snapshot = ROOT / action["path"]
        self.assertTrue(snapshot.is_file())
        source = snapshot.read_text(encoding="utf-8")
        self.assertIn('VERSION="0.5.3"', source)
        self.assertIn('STATE_DIR="/var/lib/tcpfit"', source)

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

    def test_fscarmen_singbox_full_local_clone(self):
        categories = {category["id"]: category["title"] for category in self.config["categories"]}
        self.assertEqual(categories.get("fscarmen_singbox"), "sing-box(fsr)")
        action = next(action for action in self.config["actions"] if action["id"] == "fscarmen_singbox_menu")
        self.assertEqual(action["kind"], "local_script")
        self.assertEqual(action["path"], "scripts/fscarmen-sing-box.sh")
        self.assertTrue(action["needs_root"])
        self.assertIn("bash <(wget -qO- fscarmen/sing-box.sh)", action["title"])

        snapshot = ROOT / action["path"]
        self.assertTrue(snapshot.is_file())
        source = snapshot.read_text(encoding="utf-8")
        self.assertIn("VERSION='v1.3.20 (2026.08.07)'", source)
        self.assertIn('PROTOCOL_LIST=("XTLS + reality"', source)
        self.assertNotIn("YJL-TUI", source)
        self.assertNotIn("--YJL-TUI-VLESS-WS-TLS", source)

    def test_docker_mirror_switch_is_local_docker_menu_entry(self):
        actions = {action["id"]: action for action in self.config["actions"]}
        action = actions["docker_mirror_switch"]
        self.assertEqual(action["category"], "docker_manage")
        self.assertEqual(action["kind"], "local_script")
        self.assertEqual(action["path"], "scripts/docker-mirror-switch.sh")
        self.assertEqual(action["title"], "16. 国内 Docker 源检测")
        self.assertTrue(action["needs_root"])
        self.assertTrue((ROOT / action["path"]).is_file())

    def test_nft_forward_is_vendored_advanced_menu_entry(self):
        actions = {action["id"]: action for action in self.config["actions"]}
        action = actions["nft_forward"]
        self.assertEqual(action["category"], "advanced")
        self.assertEqual(action["kind"], "local_script")
        self.assertEqual(action["path"], "scripts/nft-forward-install.sh")
        self.assertTrue(action["needs_root"])

        snapshot = ROOT / action["path"]
        self.assertTrue(snapshot.is_file())
        source = snapshot.read_text(encoding="utf-8")
        self.assertIn('REPO="xjetry/nft-forward"', source)
        self.assertIn('SCRIPT_REPO="${NFTF_SCRIPT_REPO:-dandan8511/dandan-tui}"', source)
        self.assertIn('SCRIPT_FILE="${NFTF_SCRIPT_FILE:-scripts/nft-forward-install.sh}"', source)
        self.assertIn('https://raw.githubusercontent.com/$SCRIPT_REPO/$SCRIPT_REF/$SCRIPT_FILE', source)

        launcher = (ROOT / "launch.sh").read_text(encoding="utf-8")
        self.assertIn("download scripts/nft-forward-install.sh", launcher)
        self.assertIn("scripts/nft-forward-install.sh", launcher)

    def test_lscpu_parser_and_cpu_profile_fields(self):
        sample = """Architecture: x86_64
CPU(s): 4
Vendor ID: GenuineIntel
Model name: Intel(R) Core(TM) i7-10700 CPU @ 2.90GHz
CPU family: 6
Model: 165
Stepping: 5
Thread(s) per core: 1
Core(s) per socket: 4
Socket(s): 1
Hypervisor vendor: VMware
Virtualization type: full
L1d cache: 128 KiB (4 instances)
Flags: fpu vmx avx2
"""
        parsed = tui.parse_lscpu(sample)
        self.assertEqual(parsed["Model name"], "Intel(R) Core(TM) i7-10700 CPU @ 2.90GHz")
        self.assertEqual(parsed["Hypervisor vendor"], "VMware")
        self.assertEqual(parsed["Flags"], "fpu vmx avx2")

        profile = tui.cpu_hardware_profile()
        for field in ("逻辑 CPU", "CPU 架构", "CPU 厂商", "CPU 型号", "CPU 虚拟化支持", "指令集"):
            self.assertIn(field, profile)
            self.assertTrue(profile[field])

    def test_virtualization_profile_has_detection_and_dmi_fields(self):
        profile = tui.virtualization_profile({"虚拟化厂商": "未检测到", "指令集": ""})
        for field in ("虚拟化环境", "运行形态", "虚拟机检测", "容器检测", "DMI 产品型号", "宿主机 CPU 读取"):
            self.assertIn(field, profile)
            self.assertTrue(profile[field])

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
        subprocess.run(["bash", "-n", "scripts/fscarmen-sing-box.sh"], cwd=ROOT, check=True)
        subprocess.run(["bash", "-n", "scripts/tcpfit/tcpfit.sh"], cwd=ROOT, check=True)
        subprocess.run(["bash", "-n", "scripts/docker-mirror-switch.sh"], cwd=ROOT, check=True)
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
        self.assertIn("fscarmen_singbox_menu", listing.stdout)
        self.assertNotIn("[danger]", listing.stdout)
        self.assertNotIn("[warn]", listing.stdout)
        self.assertNotIn("[safe]", listing.stdout)

    def test_input_and_name_validation(self):
        self.assertTrue(tui.TUI.docker_name("nginx:latest", image=True))
        self.assertFalse(tui.TUI.docker_name("bad name"))
        self.assertEqual(tui.safe_name("中文 action"), "action")
        self.assertIsNotNone(tui.TUI.builtin("domain_latency"))
        self.assertTrue(tui.TUI({"categories": [], "actions": []}).confirm({"risk": "danger"}))


if __name__ == "__main__":
    unittest.main()
