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
        supported_kinds = {"online", "tcp_online", "exit"}
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


class LocalBehaviorTests(unittest.TestCase):
    def test_shell_and_python_syntax(self):
        subprocess.run(["bash", "-n", "launch.sh"], cwd=ROOT, check=True)
        subprocess.run(["bash", "-n", "run.sh"], cwd=ROOT, check=True)
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
