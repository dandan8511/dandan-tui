import json
import socket
import threading
import unittest
from pathlib import Path

from singbox_manager import (
    ManagerError,
    SocksProxy,
    build_dns_fragment,
    build_managed_route_fragment,
    normalize_state,
    parse_socks5_url,
    probe_socks5,
    strip_jsonc_comments,
)


class SocksUrlTests(unittest.TestCase):
    def test_parse_authenticated_url_and_decode_credentials(self):
        proxy = parse_socks5_url("socks5://name%40example:p%3Ass@proxy.example:10308", "ovh-openai")

        self.assertEqual(proxy.tag, "ovh-openai")
        self.assertEqual(proxy.server, "proxy.example")
        self.assertEqual(proxy.server_port, 10308)
        self.assertEqual(proxy.username, "name@example")
        self.assertEqual(proxy.password, "p:ss")
        self.assertNotIn("p:ss", proxy.masked())

    def test_parse_rejects_invalid_scheme_port_and_partial_authentication(self):
        for value in (
            "http://proxy.example:1080",
            "socks5://proxy.example",
            "socks5://user@proxy.example:1080",
            "socks5://proxy.example:70000",
        ):
            with self.subTest(value=value):
                with self.assertRaises(ManagerError):
                    parse_socks5_url(value, "proxy")


class FragmentTests(unittest.TestCase):
    def test_jsonc_comments_do_not_hide_urls_or_configured_inbounds(self):
        content = '''// metadata
        {"url":"https://example.invalid/path", /* normal comment */ "inbounds":[{"tag":"test","listen_port":10118}]}'''
        data = json.loads(strip_jsonc_comments(content))
        self.assertEqual(data["url"], "https://example.invalid/path")
        self.assertEqual(data["inbounds"][0]["listen_port"], 10118)

    def test_route_fragment_uses_unique_yjl_ruleset_tags(self):
        state = {
            "version": 1,
            "socks": [SocksProxy("ovh-openai", "127.0.0.1", 1080, "user", "password").__dict__],
            "routes": {"geosite-openai": "ovh-openai", "geosite-anthropic": "direct"},
            "dns_strategy": None,
        }
        fragment = build_managed_route_fragment(state, Path("/etc/sing-box/rules"))

        self.assertEqual(fragment["outbounds"][0]["tag"], "ovh-openai")
        tags = [item["tag"] for item in fragment["route"]["rule_set"]]
        self.assertEqual(tags, ["yjl-geosite-anthropic", "yjl-geosite-openai"])
        self.assertNotIn("geosite-openai", tags)
        self.assertEqual(fragment["route"]["rules"][1]["outbound"], "ovh-openai")
        self.assertEqual(fragment["route"]["rule_set"][1]["path"], "/etc/sing-box/rules/geosite-openai.srs")

    def test_dns_and_state_normalization(self):
        self.assertEqual(build_dns_fragment("prefer_ipv4"), {"dns": {"strategy": "prefer_ipv4"}})
        self.assertIsNone(build_dns_fragment(None))

        state = normalize_state(
            {
                "socks": [
                    {"tag": "ok", "server": "127.0.0.1", "server_port": 1080, "username": "", "password": ""},
                    {"tag": "bad", "server": "127.0.0.1", "server_port": 1080, "username": "only", "password": ""},
                ],
                "routes": {"geosite-openai": "ok", "bad name": "direct"},
                "dns_strategy": "ipv6_only",
            }
        )
        self.assertEqual([item["tag"] for item in state["socks"]], ["ok"])
        self.assertEqual(state["routes"], {"geosite-openai": "ok"})
        self.assertEqual(state["dns_strategy"], "ipv6_only")


class SocksProbeTests(unittest.TestCase):
    def test_authenticated_socks_connect_probe(self):
        ready = threading.Event()
        result = {}

        def server() -> None:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
                listener.bind(("127.0.0.1", 0))
                listener.listen(1)
                result["port"] = listener.getsockname()[1]
                ready.set()
                conn, _ = listener.accept()
                with conn:
                    self.assertEqual(conn.recv(4), b"\x05\x02\x02\x00")
                    conn.sendall(b"\x05\x02")
                    auth = conn.recv(1024)
                    self.assertEqual(auth, b"\x01\x04user\x04pass")
                    conn.sendall(b"\x01\x00")
                    request = conn.recv(1024)
                    self.assertEqual(request[:4], b"\x05\x01\x00\x03")
                    self.assertIn(b"api.openai.com", request)
                    conn.sendall(b"\x05\x00\x00\x01\x00\x00\x00\x00\x00\x00")

        thread = threading.Thread(target=server, daemon=True)
        thread.start()
        self.assertTrue(ready.wait(2))
        proxy = SocksProxy("test", "127.0.0.1", result["port"], "user", "pass")
        self.assertIn("已通过", probe_socks5(proxy, timeout=2))
        thread.join(2)


if __name__ == "__main__":
    unittest.main()
