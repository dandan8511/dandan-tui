#!/usr/bin/env python3
"""YJL Linux TUI: data-driven curses menu for this workspace's scripts."""
from __future__ import annotations

import curses
import getpass
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
WORKSPACE = APP_DIR.parent
CONFIG = APP_DIR / "scripts.json"
IS_ROOT = os.geteuid() == 0
CACHE = Path(os.environ.get("YJL_TUI_CACHE_DIR", "/var/cache/yjl-tui" if IS_ROOT else Path.home() / ".cache/yjl-tui"))
LOGS = Path(os.environ.get("YJL_TUI_LOG_DIR", "/var/log/yjl-tui" if IS_ROOT else Path.home() / ".local/state/yjl-tui"))
STATE = Path(os.environ.get("YJL_TUI_STATE_DIR", "/var/lib/yjl-tui" if IS_ROOT else Path.home() / ".local/state/yjl-tui"))
WELCOME_MARKER = STATE / ".welcome-shown"

DOMAIN_LATENCY_HOSTS = [
    "gateway.icloud.com", "itunes.apple.com", "swdist.apple.com", "swcdn.apple.com",
    "updates.cdn-apple.com", "mensura.cdn-apple.com", "osxapps.itunes.apple.com",
    "aod.itunes.apple.com", "download-installer.cdn.mozilla.net", "addons.mozilla.org",
    "s0.awsstatic.com", "d1.awsstatic.com", "cdn-dynmedia-1.microsoft.com",
    "images-na.ssl-images-amazon.com", "m.media-amazon.com", "player.live-video.net",
    "one-piece.com", "lol.secure.dyn.riotcdn.net", "www.lovelive-anime.jp",
    "academy.nvidia.com", "software.download.prss.microsoft.com", "dl.google.com",
    "www.google-analytics.com", "www.caltech.edu", "www.calstatela.edu", "www.suny.edu",
    "www.suffolk.edu", "www.python.org", "vuejs-jp.org", "vuejs.org", "zh-hk.vuejs.org",
    "react.dev", "www.java.com", "www.oracle.com", "www.mysql.com", "www.mongodb.com",
    "redis.io", "cname.vercel-dns.com", "vercel-dns.com", "www.swift.com", "www.cisco.com",
    "www.asus.com", "www.samsung.com", "www.amd.com", "www.umcg.nl", "www.fom-international.com",
    "www.u-can.co.jp", "github.io",
]


def read_config() -> dict:
    with CONFIG.open(encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data.get("categories"), list) or not isinstance(data.get("actions"), list):
        raise ValueError("scripts.json must contain categories and actions")
    return data


def safe_name(value: str) -> str:
    value = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-")
    return value[:60] or "action"


def find_local(name: str) -> Path | None:
    # Do not hard-code the damaged UTF-8 directory name.
    found = sorted(p for p in WORKSPACE.rglob(name) if p.is_file() and ".git" not in p.parts)
    return found[0] if found else None


def read_os_release() -> dict[str, str]:
    values: dict[str, str] = {}
    path = Path("/etc/os-release")
    if not path.is_file():
        return values
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        if "=" not in line or line.startswith("#"):
            continue
        key, value = line.split("=", 1)
        values[key] = value.strip().strip('"')
    return values


def system_profile() -> dict[str, str]:
    osr = read_os_release()
    if shutil.which("systemctl") and Path("/run/systemd/system").exists():
        init = "systemd"
    elif shutil.which("rc-service"):
        init = "openrc"
    else:
        init = "unknown"
    if shutil.which("apt-get"):
        package_manager = "apt"
    elif shutil.which("apk"):
        package_manager = "apk"
    elif shutil.which("dnf"):
        package_manager = "dnf"
    elif shutil.which("yum"):
        package_manager = "yum"
    else:
        package_manager = "unknown"
    def active_service(name: str) -> bool:
        return bool(shutil.which("systemctl") and subprocess.run(
            ["systemctl", "is-active", "--quiet", name], check=False
        ).returncode == 0)

    if active_service("NetworkManager.service"):
        network = "NetworkManager"
    elif active_service("systemd-networkd.service"):
        network = "systemd-networkd"
    elif active_service("networking.service"):
        network = "ifupdown"
    elif init == "openrc" and shutil.which("rc-service"):
        network = "OpenRC networking"
    elif Path("/etc/netplan").is_dir():
        network = "netplan（待确认后端）"
    elif Path("/etc/network/interfaces").is_file() or Path("/etc/network/interfaces.d").is_dir():
        network = "ifupdown（服务状态未知）"
    else:
        network = "未识别"
    return {
        "系统": osr.get("PRETTY_NAME", osr.get("ID", "未知")),
        "系统 ID": osr.get("ID", "未知"),
        "版本": osr.get("VERSION_ID", "未知"),
        "初始化": init,
        "包管理器": package_manager,
        "网络管理": network,
        "架构": subprocess.run(["uname", "-m"], text=True, capture_output=True).stdout.strip() or "未知",
        "内核": subprocess.run(["uname", "-r"], text=True, capture_output=True).stdout.strip() or "未知",
        "启动模式": "UEFI" if Path("/sys/firmware/efi").exists() else "BIOS/未知",
    }


def terminal_notice() -> None:
    """Show the one-time splash outside curses so it works on tiny terminals too."""
    if WELCOME_MARKER.exists():
        return
    print("\033[36m")
    print("+----------------------------------------------------------+")
    print("|                                                          |")
    print("|              YJL 专用脚本                               |")
    print("|          Linux TUI Server Manager                        |")
    print("|                                                          |")
    print("+----------------------------------------------------------+")
    print("\033[0m", end="", flush=True)
    time.sleep(1)
    try:
        STATE.mkdir(parents=True, exist_ok=True)
        WELCOME_MARKER.touch(exist_ok=True)
    except OSError:
        # A read-only host should still be able to use the menu.
        pass


class TUI:
    def __init__(self, config: dict):
        self.categories = config["categories"]
        self.actions = config["actions"]
        self.category = 0
        self.selected = 0
        self.status = "方向键选择，Enter 执行，Tab/左右切换分类，q 退出"

    def current_actions(self) -> list[dict]:
        cid = self.categories[self.category]["id"]
        return [a for a in self.actions if a.get("category") == cid]

    def log_file(self, action_id: str) -> Path:
        LOGS.mkdir(parents=True, exist_ok=True)
        return LOGS / f"{time.strftime('%Y%m%d-%H%M%S')}-{safe_name(action_id)}.log"

    @staticmethod
    def run(cmd: list[str], capture: bool = False, input_text: str | None = None):
        return subprocess.run(cmd, text=True, capture_output=capture, input=input_text, env=os.environ.copy())

    @staticmethod
    def root_required() -> bool:
        if IS_ROOT:
            return True
        print("此功能需要 root 权限，请使用 sudo 或 root 登录后启动 TUI。")
        return False

    @staticmethod
    def backup_file(path: Path) -> Path | None:
        if not path.exists():
            return None
        backup = path.with_name(path.name + ".yjl-tui.bak." + time.strftime("%Y%m%d-%H%M%S"))
        shutil.copy2(path, backup)
        return backup

    @staticmethod
    def interfaces() -> list[str]:
        if not shutil.which("ip"):
            return []
        result = subprocess.run(["ip", "-o", "link", "show"], text=True, capture_output=True)
        names = []
        for line in result.stdout.splitlines():
            match = re.match(r"\d+:\s+([^:]+):", line)
            if match and match.group(1) != "lo":
                names.append(match.group(1).split("@", 1)[0])
        return names

    def system_info(self, log: Path) -> int:
        print("== 系统能力 ==")
        for key, value in system_profile().items():
            print(f"{key}: {value}")
        print("\n== CPU / 内存 ==")
        if shutil.which("nproc"):
            subprocess.run(["nproc"])
        if shutil.which("free"):
            subprocess.run(["free", "-h"])
        print("\n== 网卡地址 ==")
        if shutil.which("ip"):
            subprocess.run(["ip", "-brief", "address"])
            print("\n== 路由 ==")
            subprocess.run(["ip", "route", "show"])
        print("\n== 磁盘 ==")
        subprocess.run(["df", "-hT"])
        print(f"\n数据目录：{STATE}\n日志目录：{LOGS}\n缓存目录：{CACHE}")
        return 0

    def system_upgrade(self, log: Path) -> int:
        if not self.root_required():
            return 1
        manager = system_profile()["包管理器"]
        if manager == "apt":
            result = subprocess.run(["apt-get", "update"])
            if result.returncode == 0:
                result = subprocess.run(["apt-get", "upgrade", "-y"])
            return result.returncode
        if manager == "apk":
            result = subprocess.run(["apk", "update"])
            if result.returncode == 0:
                result = subprocess.run(["apk", "upgrade"])
            return result.returncode
        print("当前系统未识别到 apt/apk，未执行系统更新。")
        return 1

    def log_manage(self, log: Path) -> int:
        print("== 日志与磁盘策略 ==")
        if LOGS.exists():
            subprocess.run(["du", "-sh", str(LOGS)])
        if shutil.which("journalctl"):
            subprocess.run(["journalctl", "--disk-usage"])
        print("\n1. 只查看占用\n2. 配置小硬盘策略（TUI 2MB/文件、最多 3 个；journal 50MB/7天）")
        choice = input("选择 [1]: ").strip() or "1"
        if choice == "1":
            return 0
        if choice != "2" or not self.root_required():
            return 1
        try:
            Path("/etc/logrotate.d").mkdir(parents=True, exist_ok=True)
            rotate = Path("/etc/logrotate.d/yjl-tui")
            rotate.write_text(
                f"{LOGS}/*.log {{\n    size 2M\n    rotate 3\n    daily\n    compress\n    missingok\n    notifempty\n    copytruncate\n}}\n",
                encoding="utf-8",
            )
            print(f"已写入：{rotate}")
            if system_profile()["初始化"] == "systemd":
                dropin = Path("/etc/systemd/journald.conf.d/99-yjl-tui.conf")
                dropin.parent.mkdir(parents=True, exist_ok=True)
                dropin.write_text(
                    "[Journal]\nSystemMaxUse=50M\nRuntimeMaxUse=20M\nMaxRetentionSec=7day\n",
                    encoding="utf-8",
                )
                subprocess.run(["systemctl", "restart", "systemd-journald"], check=False)
                subprocess.run(["journalctl", "--vacuum-size=50M", "--vacuum-time=7d"], check=False)
                print(f"已写入：{dropin}")
            else:
                print("当前不是 systemd，已配置 TUI 日志轮转；没有修改 Alpine/OpenRC 的日志服务。")
            print("策略已启用。它限制日志容量，不会关闭必要的错误日志。")
            return 0
        except OSError as exc:
            print(f"写入日志策略失败：{exc}")
            return 1

    def kernel_manage(self, log: Path) -> int:
        if not self.root_required():
            return 1
        profile = system_profile()
        print(f"系统：{profile['系统']}\n当前内核：{profile['内核']}\n包管理器：{profile['包管理器']}")
        print("\n1. 查看可用官方内核\n2. 安装指定官方内核\n3. 更新内核源并查看可用内核")
        choice = input("选择 [1]: ").strip() or "1"
        if choice not in {"1", "2", "3"}:
            return 2
        if profile["包管理器"] == "apt":
            if choice == "3":
                subprocess.run(["apt-get", "update"])
            result = subprocess.run(
                ["bash", "-c", "apt-cache search '^linux-(image|headers|modules|generic)' | sed -n '1,80p'"],
                text=True,
            )
            if choice == "1" or choice == "3":
                print("\n以上是系统源内可见的内核相关包。常见安全选择是 linux-image-amd64 或 linux-generic。")
                return result.returncode
            package = input("输入要安装的包名（例如 linux-image-amd64）：").strip()
            if not re.fullmatch(r"linux-[A-Za-z0-9.+:~-]+", package):
                print("包名格式不合法，只允许 linux- 开头的官方内核包。")
                return 2
            result = subprocess.run(["apt-get", "install", "-y", package])
        elif profile["包管理器"] == "apk":
            if choice == "3":
                subprocess.run(["apk", "update"])
            result = subprocess.run(["apk", "search", "-v", "linux-"])
            if choice == "1" or choice == "3":
                return result.returncode
            package = input("输入要安装的 Alpine 内核包名：").strip()
            if not re.fullmatch(r"linux-[A-Za-z0-9.+_-]+", package):
                print("包名格式不合法，只允许 linux- 开头的官方内核包。")
                return 2
            result = subprocess.run(["apk", "add", package])
        else:
            print("当前系统暂未识别到 apt/apk，无法安全列出官方内核。")
            return 1
        if result.returncode == 0:
            if shutil.which("update-grub"):
                subprocess.run(["update-grub"])
            print("内核包安装完成。请先确认新内核已经出现在 GRUB，再决定是否重启。")
        return result.returncode

    @staticmethod
    def nginx_config_text() -> str:
        if not shutil.which("nginx"):
            return ""
        result = subprocess.run(["nginx", "-T"], text=True, capture_output=True)
        return result.stdout + result.stderr if result.returncode == 0 else ""

    def nginx_domains(self) -> list[str]:
        text = self.nginx_config_text()
        domains: set[str] = set()
        for line in text.splitlines():
            line = line.split("#", 1)[0]
            match = re.search(r"\bserver_name\s+([^;]+);", line)
            if match:
                for value in match.group(1).split():
                    if re.fullmatch(r"(?:\*\.)?[A-Za-z0-9.-]+", value) and "." in value:
                        domains.add(value)
        return sorted(domains)

    def write_nginx_ssl_config(self, domain: str, cert_dir: Path) -> tuple[bool, Path | None]:
        """Add certificate directives to the existing server block when it is unambiguous."""
        candidates = []
        for root in (Path("/etc/nginx"), Path("/usr/local/nginx/conf")):
            if root.is_dir():
                candidates.extend(path for path in root.rglob("*.conf") if path.is_file())
        domain_pattern = rf"(?m)^\s*server_name\s+[^;]*{re.escape(domain)}(?:\s|;)"
        path = next((candidate for candidate in sorted(set(candidates)) if re.search(domain_pattern, candidate.read_text(encoding="utf-8", errors="ignore"))), None)
        if not path:
            print(f"没有找到包含 {domain} 的 Nginx 配置文件，证书已安装但未自动改写站点配置。")
            return False, None
        original = path.read_text(encoding="utf-8", errors="surrogateescape")
        domain_line = re.search(domain_pattern, original)
        server_match = re.search(r"(?m)^\s*server\s*\{", original[: domain_line.start() + 1] if domain_line else original)
        if not server_match:
            print(f"无法定位 {domain} 所属 server 块，证书已安装但未自动改写配置。")
            return False, None
        start = server_match.start()
        depth = 0
        closing = None
        for index in range(server_match.end() - 1, len(original)):
            if original[index] == "{":
                depth += 1
            elif original[index] == "}":
                depth -= 1
                if depth == 0:
                    closing = index
                    break
        if closing is None:
            print("Nginx server 块括号不完整，已停止改写。")
            return False, None
        block = original[start:closing]
        cert = cert_dir / "fullchain.pem"
        key = cert_dir / "privkey.pem"
        additions = []
        if not re.search(r"(?m)^\s*listen\s+[^;]*443", block):
            additions.append("    listen 443 ssl;")
        if re.search(r"(?m)^\s*ssl_certificate\s+[^;]+;", block):
            block = re.sub(r"(?m)^\s*ssl_certificate\s+[^;]+;", f"    ssl_certificate {cert};", block, count=1)
        else:
            additions.append(f"    ssl_certificate {cert};")
        if re.search(r"(?m)^\s*ssl_certificate_key\s+[^;]+;", block):
            block = re.sub(r"(?m)^\s*ssl_certificate_key\s+[^;]+;", f"    ssl_certificate_key {key};", block, count=1)
        else:
            additions.append(f"    ssl_certificate_key {key};")
        if not re.search(r"(?m)^\s*ssl_protocols\s+", block):
            additions.append("    ssl_protocols TLSv1.2 TLSv1.3;")
        if additions:
            block = block[: block.find("\n") + 1] + "\n".join(additions) + "\n" + block[block.find("\n") + 1 :]
        updated = original[:start] + block + original[closing:]
        backup = self.backup_file(path)
        path.write_text(updated, encoding="utf-8", errors="surrogateescape")
        result = subprocess.run(["nginx", "-t"], text=True)
        if result.returncode:
            if backup:
                shutil.copy2(backup, path)
            print(f"nginx -t 失败，已恢复配置；备份：{backup}")
            return False, backup
        subprocess.run(["nginx", "-s", "reload"], check=False)
        return True, backup

    def ensure_acme(self, email: str, log: Path) -> Path | None:
        acme_home = Path.home() / ".acme.sh"
        acme = acme_home / "acme.sh"
        if acme.is_file():
            return acme
        if not shutil.which("curl"):
            print("缺少 curl，无法安装 acme.sh。")
            return None
        CACHE.mkdir(parents=True, exist_ok=True)
        installer = CACHE / "acme.sh"
        result = subprocess.run(["curl", "-fL", "--retry", "3", "https://raw.githubusercontent.com/acmesh-official/acme.sh/master/acme.sh", "-o", str(installer)], text=True, capture_output=True)
        if result.returncode:
            print(result.stderr.strip() or "acme.sh 下载失败")
            return None
        installer.chmod(0o700)
        if not self.syntax_ok(installer, "bash"):
            return None
        result = subprocess.run(["bash", str(installer), "--install", "--home", str(acme_home), "--accountemail", email], text=True)
        if result.returncode or not acme.is_file():
            print("acme.sh 安装失败，请查看本次操作日志。")
            return None
        return acme

    def ssl_manage(self, log: Path) -> int:
        if not self.root_required():
            return 1
        if not shutil.which("nginx"):
            print("没有检测到 nginx。请先安装或确认 nginx 在 PATH 中。")
            return 1
        domains = self.nginx_domains()
        print("检测到的 Nginx 域名：")
        for index, domain in enumerate(domains, 1):
            print(f"  {index}. {domain}")
        print("\n1. 查看证书状态\n2. 申请新证书\n3. 批量续期")
        choice = input("选择 [1]: ").strip() or "1"
        if choice == "1":
            text = self.nginx_config_text()
            certs = sorted(set(re.findall(r"\bssl_certificate\s+([^;]+);", text)))
            if not certs:
                print("当前 Nginx 配置没有发现 ssl_certificate。")
            for cert in certs:
                path = Path(cert.strip())
                print(f"\n证书：{path}")
                if path.is_file():
                    subprocess.run(["openssl", "x509", "-in", str(path), "-noout", "-subject", "-issuer", "-dates"], check=False)
                else:
                    print("文件不存在")
            return 0
        if choice == "3":
            acme = Path.home() / ".acme.sh" / "acme.sh"
            if not acme.is_file():
                print("未检测到 acme.sh 续期任务。")
                return 1
            result = subprocess.run([str(acme), "--cron", "--home", str(acme.parent)])
            if result.returncode == 0:
                subprocess.run(["nginx", "-t"], check=False)
            return result.returncode
        if choice != "2":
            return 2
        selected = input("输入域名编号（逗号分隔），或直接输入域名：").strip()
        if not selected:
            print("没有输入域名。")
            return 2
        if all(part.strip().isdigit() for part in selected.split(",")) and domains:
            chosen = [domains[int(part.strip()) - 1] for part in selected.split(",") if 0 < int(part.strip()) <= len(domains)]
        else:
            chosen = selected.split()
        chosen = [d for d in chosen if re.fullmatch(r"(?:\*\.)?[A-Za-z0-9.-]+", d) and "." in d]
        if not chosen:
            print("域名格式不合法。")
            return 2
        print("\n域名解析预检查：")
        for domain in chosen:
            result = subprocess.run(["getent", "ahosts", domain], text=True, capture_output=True) if shutil.which("getent") else None
            if result and result.returncode == 0:
                addresses = sorted({line.split()[0] for line in result.stdout.splitlines() if line.split()})
                print(f"  {domain}: {', '.join(addresses[:6])}")
            else:
                print(f"  {domain}: 未解析到地址，申请大概率失败")
        email = input("申请邮箱：").strip()
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
            print("邮箱格式不合法。")
            return 2
        method = input("验证方式：1 HTTP-01  2 DNS-01 [1]: ").strip() or "1"
        if method not in {"1", "2"}:
            return 2
        if method == "1":
            if self.listening("80"):
                print("本机 TCP 80 已有监听，acme.sh --nginx 会尝试临时接管 Nginx；请确认外部 80 也能访问。")
            else:
                print("本机没有监听 TCP 80；HTTP-01 大概率会失败，建议使用 DNS-01。")
        env: dict[str, str] = {}
        dns_provider = ""
        if method == "2":
            dns_provider = input("DNS 服务商 [cloudflare/aliyun/tencent]：").strip().lower()
            token = getpass.getpass("DNS API Token/Secret（不会写入命令行日志）：")
            if not token:
                print("DNS 凭据为空。")
                return 2
            if dns_provider == "cloudflare":
                env["CF_Token"] = token
            elif dns_provider == "aliyun":
                env["Ali_Key"] = token.split("|", 1)[0]
                env["Ali_Secret"] = token.split("|", 1)[1] if "|" in token else getpass.getpass("Ali_Secret：")
            elif dns_provider == "tencent":
                env["Tencent_SecretId"] = token.split("|", 1)[0]
                env["Tencent_SecretKey"] = token.split("|", 1)[1] if "|" in token else getpass.getpass("Tencent_SecretKey：")
            else:
                print("目前内置 DNS API 仅支持 cloudflare、aliyun、tencent；可先手动配置 acme.sh。")
                return 2
        acme = self.ensure_acme(email, log)
        if not acme:
            return 1
        nginx_dir = Path("/etc/nginx")
        backup = STATE / ("nginx-backup-" + time.strftime("%Y%m%d-%H%M%S") + ".tar.gz")
        STATE.mkdir(parents=True, exist_ok=True)
        if nginx_dir.is_dir():
            subprocess.run(["tar", "-czf", str(backup), "-C", "/etc", "nginx"], check=False)
            print(f"Nginx 配置备份：{backup}")
        primary = chosen[0].replace("*.", "wildcard-")
        cert_dir = Path("/etc/ssl/yjl-tui") / primary
        cert_dir.mkdir(parents=True, exist_ok=True)
        issue = [str(acme), "--issue", "--home", str(acme.parent)]
        for domain in chosen:
            issue.extend(["-d", domain])
        if method == "1":
            issue.append("--nginx")
        else:
            issue.extend(["--dns", {"cloudflare": "dns_cf", "aliyun": "dns_ali", "tencent": "dns_tencent"}[dns_provider]])
        issue.extend(["--server", "letsencrypt"])
        result = subprocess.run(issue, env={**os.environ, **env})
        if result.returncode:
            print("证书申请失败，Nginx 原配置尚未写入。")
            return result.returncode
        install = [str(acme), "--install-cert", "-d", chosen[0], "--home", str(acme.parent), "--key-file", str(cert_dir / "privkey.pem"), "--fullchain-file", str(cert_dir / "fullchain.pem"), "--reloadcmd", "nginx -t && nginx -s reload"]
        result = subprocess.run(install, env={**os.environ, **env})
        if result.returncode:
            print("证书已申请，但安装到 Nginx 失败；请检查备份和证书目录。")
            return result.returncode
        if input("自动写入现有 Nginx server 块并 reload？[Y/n] ").strip().lower() not in {"n", "no"}:
            configured, config_backup = self.write_nginx_ssl_config(chosen[0], cert_dir)
            if config_backup:
                print(f"Nginx 配置备份：{config_backup}")
            if not configured:
                print("证书申请和安装已完成，但请手动确认 server_name 对应站点的 SSL 指令。")
        print(f"证书路径：{cert_dir}\n续期方式：acme.sh cron\nNginx 配置备份：{backup}")
        return 0

    @staticmethod
    def network_service_active(name: str) -> bool:
        return bool(shutil.which("systemctl") and subprocess.run(
            ["systemctl", "is-active", "--quiet", name], check=False
        ).returncode == 0)

    def network_backend(self, iface: str) -> str:
        """Detect the active manager for this interface, not merely installed commands."""
        if shutil.which("nmcli"):
            connection = subprocess.run(
                ["nmcli", "-g", "GENERAL.CONNECTION", "device", "show", iface],
                text=True,
                capture_output=True,
            ).stdout.strip()
            if connection and connection != "--" and (
                self.network_service_active("NetworkManager.service") or not shutil.which("systemctl")
            ):
                return "NetworkManager"
        if self.network_service_active("systemd-networkd.service") and shutil.which("networkctl"):
            return "systemd-networkd"
        if self.network_service_active("networking.service") and Path("/etc/network/interfaces").is_file():
            return "ifupdown"
        if shutil.which("rc-service") and Path("/etc/network/interfaces").is_file():
            status = subprocess.run(["rc-service", "networking", "status"], check=False)
            if status.returncode == 0:
                return "OpenRC networking"
        if shutil.which("netplan") and Path("/etc/netplan").is_dir():
            return "netplan"
        return "未识别"

    @staticmethod
    def current_network_values(iface: str) -> tuple[str, str, list[str]]:
        address = ""
        gateway = ""
        dns: list[str] = []
        if shutil.which("ip"):
            addr = subprocess.run(
                ["ip", "-4", "-o", "addr", "show", "dev", iface, "scope", "global"],
                text=True,
                capture_output=True,
            ).stdout
            match = re.search(r"\binet\s+(\S+)", addr)
            address = match.group(1) if match else ""
            routes = subprocess.run(
                ["ip", "-4", "route", "show", "default", "dev", iface],
                text=True,
                capture_output=True,
            ).stdout
            match = re.search(r"\bvia\s+(\S+)", routes)
            gateway = match.group(1) if match else ""
        resolv = Path("/etc/resolv.conf")
        if resolv.is_file():
            for line in resolv.read_text(encoding="utf-8", errors="ignore").splitlines():
                match = re.match(r"\s*nameserver\s+(\S+)", line)
                if match and match.group(1) not in {"127.0.0.53", "127.0.0.1", "::1"}:
                    dns.append(match.group(1))
        if not dns and shutil.which("resolvectl"):
            result = subprocess.run(["resolvectl", "dns", iface], text=True, capture_output=True)
            for item in result.stdout.split(":", 1)[-1].split():
                if re.fullmatch(r"\d{1,3}(?:\.\d{1,3}){3}", item):
                    dns.append(item)
        return address, gateway, dns

    @staticmethod
    def network_prompt(current: tuple[str, str, list[str]]) -> tuple[str, str, list[str]] | None:
        import ipaddress

        old_address, old_gateway, old_dns = current
        print("直接按 Enter 保留当前值；输入 - 可清空网关或 DNS。")
        cidr = input(f"IPv4 地址/掩码 [{old_address or '必填，例如 10.0.1.30/24'}]: ").strip() or old_address
        gateway_value = input(f"IPv4 网关 [{old_gateway or '无'}]: ").strip()
        gateway = old_gateway if not gateway_value else ("" if gateway_value == "-" else gateway_value)
        dns_value = input(f"DNS，多个用逗号分隔 [{','.join(old_dns) or '无'}]: ").strip()
        dns_text = ",".join(old_dns) if not dns_value else ("" if dns_value == "-" else dns_value)
        dns = [item.strip() for item in dns_text.split(",") if item.strip()]
        try:
            address = ipaddress.ip_interface(cidr)
            if address.version != 4 or (gateway and ipaddress.ip_address(gateway).version != 4):
                raise ValueError
            for item in dns:
                if ipaddress.ip_address(item).version != 4:
                    raise ValueError
        except (ValueError, TypeError):
            print("IPv4 地址、网关或 DNS 格式不合法。")
            return None
        return cidr, gateway, dns

    def apply_network_config(self, iface: str, backend: str, cidr: str, gateway: str, dns: list[str]) -> int:
        if backend == "NetworkManager":
            connection = subprocess.run(
                ["nmcli", "-g", "GENERAL.CONNECTION", "device", "show", iface],
                text=True,
                capture_output=True,
            ).stdout.strip()
            if not connection or connection == "--":
                print("没有找到该网卡对应的 NetworkManager 连接。")
                return 1
            command = ["nmcli", "connection", "modify", connection, "ipv4.method", "manual", "ipv4.addresses", cidr, "ipv4.gateway", gateway]
            command.extend(["ipv4.dns", ",".join(dns)] if dns else ["ipv4.dns", "", "ipv4.ignore-auto-dns", "no"])
            result = subprocess.run(command)
            if result.returncode == 0:
                print("配置已保存，正在重新激活 NetworkManager 连接；SSH 可能会断开。")
                result = subprocess.run(["nmcli", "connection", "up", connection])
            return result.returncode

        if backend == "netplan":
            if not shutil.which("netplan"):
                print("未找到 netplan 命令，未修改。")
                return 1
            path = Path("/etc/netplan/99-yjl-tui.yaml")
            path.parent.mkdir(parents=True, exist_ok=True)
            backup = self.backup_file(path)
            content = f"network:\n  version: 2\n  ethernets:\n    {iface}:\n      dhcp4: false\n      addresses: [{cidr}]\n      accept-ra: true\n"
            if gateway:
                content += f"      routes:\n        - to: default\n          via: {gateway}\n"
            if dns:
                content += "      nameservers:\n        addresses: [" + ", ".join(dns) + "]\n"
            path.write_text(content, encoding="utf-8")
            result = subprocess.run(["netplan", "generate"])
            if result.returncode == 0:
                print("配置校验通过，正在应用 netplan；SSH 可能会断开。")
                result = subprocess.run(["netplan", "apply"])
            if result.returncode:
                if backup:
                    shutil.copy2(backup, path)
                else:
                    path.unlink(missing_ok=True)
                print("网络配置失败，已恢复原配置。")
            else:
                print(f"配置已写入：{path}" + (f"；备份：{backup}" if backup else ""))
            return result.returncode

        if backend == "systemd-networkd":
            path = Path("/etc/systemd/network") / f"20-yjl-tui-{safe_name(iface)}.network"
            path.parent.mkdir(parents=True, exist_ok=True)
            backup = self.backup_file(path)
            content = f"[Match]\nName={iface}\n\n[Network]\nAddress={cidr}\nIPv6AcceptRA=yes\n"
            if gateway:
                content += f"Gateway={gateway}\n"
            for item in dns:
                content += f"DNS={item}\n"
            path.write_text(content, encoding="utf-8")
            result = subprocess.run(["networkctl", "reload"])
            if result.returncode == 0:
                print("配置已保存，正在重新配置网卡；SSH 可能会断开。")
                result = subprocess.run(["networkctl", "reconfigure", iface])
            if result.returncode:
                if backup:
                    shutil.copy2(backup, path)
                else:
                    path.unlink(missing_ok=True)
                print("网络配置失败，已恢复原配置。")
            else:
                print(f"配置已写入：{path}" + (f"；备份：{backup}" if backup else ""))
            return result.returncode

        if backend in {"ifupdown", "OpenRC networking"}:
            path = Path("/etc/network/interfaces.d") / f"yjl-tui-{safe_name(iface)}"
            path.parent.mkdir(parents=True, exist_ok=True)
            backup = self.backup_file(path)
            import ipaddress
            netmask = str(ipaddress.ip_interface(cidr).network.netmask)
            content = f"auto {iface}\niface {iface} inet static\n    address {cidr.split('/', 1)[0]}\n    netmask {netmask}\n"
            if gateway:
                content += f"    gateway {gateway}\n"
            if dns:
                content += f"    dns-nameservers {' '.join(dns)}\n"
            path.write_text(content, encoding="utf-8")
            main = Path("/etc/network/interfaces")
            main_backup = self.backup_file(main)
            original = main.read_text(encoding="utf-8", errors="ignore") if main.exists() else ""
            source_line = "source /etc/network/interfaces.d/*"
            if source_line not in original:
                main.write_text(original.rstrip() + "\n\n" + source_line + "\n", encoding="utf-8")
            if backend == "OpenRC networking":
                result = subprocess.run(["rc-service", "networking", "restart"])
            elif shutil.which("systemctl"):
                result = subprocess.run(["systemctl", "restart", "networking"], check=False)
            else:
                result = subprocess.CompletedProcess([], 0)
            if result.returncode:
                if backup:
                    shutil.copy2(backup, path)
                else:
                    path.unlink(missing_ok=True)
                if main_backup:
                    shutil.copy2(main_backup, main)
                print("网络服务重启失败，已恢复原配置。")
            else:
                print(f"配置已写入：{path}" + (f"；备份：{backup}" if backup else ""))
            return result.returncode

        print("没有识别出当前网卡的网络管理组件，未修改，避免误写错误配置。")
        return 1

    def network_manage(self, log: Path) -> int:
        if not self.root_required():
            return 1
        names = self.interfaces()
        if not names:
            print("没有检测到可配置网卡或缺少 ip 命令。")
            return 1
        print("当前网卡：")
        for index, name in enumerate(names, 1):
            print(f"  {index}. {name}")
        raw = input("选择网卡编号：").strip()
        if not raw.isdigit() or not 0 < int(raw) <= len(names):
            return 2
        iface = names[int(raw) - 1]
        backend = self.network_backend(iface)
        current = self.current_network_values(iface)
        print(f"\n检测到网络管理组件：{backend}")
        print(f"当前 IPv4：{current[0] or '未检测到'}")
        print(f"当前网关：{current[1] or '未检测到'}")
        print(f"当前 DNS：{', '.join(current[2]) or '未检测到'}")
        subprocess.run(["ip", "-brief", "address", "show", "dev", iface])
        print("\n1. 查看当前配置\n2. 修改 IPv4、网关和 DNS（自动使用上述组件）")
        choice = input("选择 [1]: ").strip() or "1"
        if choice == "1":
            return 0
        if choice != "2":
            return 2
        values = self.network_prompt(current)
        if values is None:
            return 2
        return self.apply_network_config(iface, backend, *values)

    def grub_manage(self, log: Path) -> int:
        if not self.root_required():
            return 1
        defaults = Path("/etc/default/grub")
        if not defaults.is_file():
            print("没有找到 /etc/default/grub，当前系统可能没有使用 GRUB。")
            return 1
        print(defaults.read_text(encoding="utf-8", errors="ignore"))
        print("1. 查看配置\n2. 修改 timeout\n3. 修改默认启动项\n4. 修改内核参数并生成 grub.cfg\n5. 恢复最近备份")
        choice = input("选择 [1]: ").strip() or "1"
        if choice == "1":
            return 0
        if choice == "5":
            backups = sorted(defaults.parent.glob("grub.yjl-tui.bak.*"), reverse=True)
            if not backups:
                print("没有找到备份。")
                return 1
            shutil.copy2(backups[0], defaults)
            print(f"已恢复：{backups[0]}")
            return 0
        value = None
        key = None
        if choice == "2":
            value = input("GRUB timeout 秒数 [5]：").strip() or "5"
            if not value.isdigit() or int(value) > 600:
                print("timeout 必须是 0 到 600 的整数。")
                return 2
            key = "GRUB_TIMEOUT"
        elif choice == "3":
            value = input("默认启动项（数字索引或 saved）：").strip()
            if not re.fullmatch(r"(?:[0-9]+|saved)", value):
                print("只允许数字索引或 saved。")
                return 2
            key = "GRUB_DEFAULT"
        elif choice == "4":
            value = input("GRUB_CMDLINE_LINUX_DEFAULT 内容：").strip()
            if any(char in value for char in "\n\r\x00"):
                return 2
            key = "GRUB_CMDLINE_LINUX_DEFAULT"
        else:
            return 2
        backup = defaults.with_name("grub.yjl-tui.bak." + time.strftime("%Y%m%d-%H%M%S"))
        shutil.copy2(defaults, backup)
        original = defaults.read_text(encoding="utf-8", errors="surrogateescape")
        replacement = f'{key}="{value}"'
        pattern = rf"(?m)^\s*#?\s*{re.escape(key)}=.*$"
        updated = re.sub(pattern, replacement, original, count=1)
        if updated == original:
            updated = original.rstrip() + "\n" + replacement + "\n"
        defaults.write_text(updated, encoding="utf-8")
        command = ["update-grub"] if shutil.which("update-grub") else ["grub-mkconfig", "-o", "/boot/grub/grub.cfg"]
        result = subprocess.run(command)
        if result.returncode:
            shutil.copy2(backup, defaults)
            print(f"生成 GRUB 配置失败，已恢复；备份：{backup}")
            return result.returncode
        print(f"已保存：{defaults}\n备份：{backup}\n注意：默认项会在下次启动时生效。")
        return 0

    def ip_preference(self, log: Path) -> int:
        if not self.root_required():
            return 1
        path = Path("/etc/gai.conf")
        print("1. IPv4 优先\n2. IPv6 优先/恢复默认")
        choice = input("选择 [1]: ").strip() or "1"
        if choice not in {"1", "2"}:
            return 2
        backup = self.backup_file(path)
        original = path.read_text(encoding="utf-8", errors="ignore") if path.exists() else ""
        lines = [line for line in original.splitlines() if not line.startswith("# Managed by yjl-tui") and not line.startswith("precedence ::ffff:0:0/96")]
        if choice == "1":
            lines.extend(["# Managed by yjl-tui", "precedence ::ffff:0:0/96  100"])
        path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
        print(f"已更新地址选择策略：{path}" + (f"；备份：{backup}" if backup else ""))
        print("这只调整地址选择优先级，不会关闭 IPv4/IPv6。可用 curl -4/-6 分别验证实际出站。")
        return 0

    def webdav_manage(self, log: Path) -> int:
        print("== WebDAV 检测 ==")
        for service in ("apache2", "httpd", "nginx", "rclone"):
            if shutil.which(service):
                print(f"命令：{service} 已存在")
        for path in (Path("/etc/apache2"), Path("/etc/httpd"), Path("/etc/nginx")):
            if path.is_dir():
                print(f"配置目录：{path}")
        if shutil.which("ss"):
            subprocess.run(["ss", "-lntup"])
        print("\n1. 重新扫描本机信息\n2. Debian/Ubuntu 安装 Apache WebDAV\n3. 查看 Apache WebDAV 相关配置")
        choice = input("选择 [1]: ").strip() or "1"
        if choice == "1":
            return 0
        if choice == "2":
            if not self.root_required():
                return 1
            profile = system_profile()
            if profile["包管理器"] != "apt":
                print("当前安装器只对 Debian/Ubuntu 做了保守适配；Alpine 请先确认 Apache 模块包名。")
                return 1
            result = subprocess.run(["apt-get", "install", "-y", "apache2", "apache2-utils"])
            if result.returncode:
                return result.returncode
            subprocess.run(["a2enmod", "dav", "dav_fs", "auth_basic"], check=False)
            data = Path("/var/lib/yjl-webdav")
            data.mkdir(parents=True, exist_ok=True)
            user = input("WebDAV 用户名 [yjl]：").strip() or "yjl"
            password = getpass.getpass("WebDAV 密码：")
            if not password:
                print("密码为空，已停止。")
                return 2
            htpasswd = Path("/etc/apache2/.yjl-webdav.htpasswd")
            if not shutil.which("htpasswd"):
                print("缺少 htpasswd，未写入 WebDAV 配置。")
                return 1
            password_result = subprocess.run(["htpasswd", "-i", "-c", str(htpasswd), user], input=password + "\n", text=True, capture_output=True)
            if password_result.returncode:
                print(password_result.stderr.strip() or "生成 WebDAV 密码文件失败。")
                return password_result.returncode
            conf = Path("/etc/apache2/conf-available/yjl-webdav.conf")
            conf.write_text(
                "Alias /webdav /var/lib/yjl-webdav\n<Directory /var/lib/yjl-webdav>\n    DAV On\n    AuthType Basic\n    AuthName \"YJL WebDAV\"\n    AuthUserFile /etc/apache2/.yjl-webdav.htpasswd\n    Require valid-user\n</Directory>\n",
                encoding="utf-8",
            )
            subprocess.run(["a2enconf", "yjl-webdav"], check=False)
            subprocess.run(["apache2ctl", "configtest"])
            result = subprocess.run(["systemctl", "reload", "apache2"])
            print("WebDAV 地址：当前主机的 http(s)://域名/webdav/")
            return result.returncode
        if choice == "3":
            for path in (Path("/etc/apache2"), Path("/etc/httpd"), Path("/etc/nginx")):
                if path.is_dir():
                    subprocess.run(["bash", "-c", f"grep -RniE 'dav|webdav' {shlex.quote(str(path))} 2>/dev/null | sed -n '1,80p'"], check=False)
            return 0
        return 2

    def tcp_status(self, log: Path) -> int:
        print("== TCP / 内核状态 ==")
        for command in (("uname", "-r"), ("sysctl", "-n", "net.ipv4.tcp_congestion_control"), ("sysctl", "-n", "net.core.default_qdisc"), ("sysctl", "-n", "net.ipv4.tcp_fastopen")):
            if shutil.which(command[0]):
                result = subprocess.run(list(command), text=True, capture_output=True)
                print(f"{' '.join(command)}: {result.stdout.strip() or result.stderr.strip()}")
        available = Path("/proc/sys/net/ipv4/tcp_allowed_congestion_control")
        if available.is_file():
            print(f"可用拥塞控制：{available.read_text(encoding='utf-8', errors='ignore').strip()}")
        if shutil.which("tc"):
            print("\n== qdisc ==")
            subprocess.run(["tc", "qdisc", "show"])
        return 0

    def tcp_tune(self, log: Path) -> int:
        if not self.root_required():
            return 1
        source = APP_DIR / "tcp-tuning.conf"
        if not source.is_file():
            print(f"没有找到本地调优参数：{source}")
            return 1
        lines = []
        for line in source.read_text(encoding="utf-8", errors="ignore").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if not re.fullmatch(r"[A-Za-z0-9_.]+\s*=\s*[^\s#]+(?:\s+[^\s#]+)*", stripped):
                print(f"调优参数格式不合法，已停止：{line}")
                return 2
            lines.append(stripped)
        if not lines:
            print("本地调优参数为空，已停止。")
            return 2
        target = Path("/etc/sysctl.d/99-yjl-tcp-tuning.conf")
        target.parent.mkdir(parents=True, exist_ok=True)
        backup = self.backup_file(target)
        target.write_text("# Managed by dandan-tui; source: tcp-tuning.conf\n" + "\n".join(lines) + "\n", encoding="utf-8")
        print(f"已写入：{target}" + (f"；备份：{backup}" if backup else ""))
        if not shutil.which("sysctl"):
            print("缺少 sysctl，参数已保存但没有应用。")
            return 1
        result = subprocess.run(["sysctl", "--system"])
        print("\n应用后的关键状态：")
        self.tcp_status(log)
        if result.returncode:
            print("部分参数可能不受当前内核支持；外部内核/依赖安装仍保留在在线 tcp.sh 入口。")
        else:
            print("本地 TCP/BBR 参数已应用，无需联网安装。")
        return result.returncode

    def confirm(self, action: dict) -> bool:
        risk = action.get("risk", "warn")
        if risk == "safe":
            return True
        print(f"\n动作：{action['title']}\n说明：{action.get('description', '')}")
        if action.get("url"):
            print(f"来源：{action['url']}")
        if risk == "danger":
            print("提示：这是高风险操作，确认后将直接执行；本版本不再要求输入 RUN。")
            return True
        return input("确认执行？[y/N] ").strip().lower() in {"y", "yes"}

    def interactive(self, cmd: list[str], log: Path, env: dict[str, str] | None = None) -> int:
        print(f"\n命令：{shlex.join(cmd)}\n日志：{log}")
        print("以下进入原脚本的真实终端交互，结束后按 Enter 返回。\n")
        merged = os.environ.copy()
        if env:
            merged.update(env)
        run_cmd = ["script", "-qefc", shlex.join(cmd), str(log)] if shutil.which("script") else cmd
        try:
            result = subprocess.run(run_cmd, env=merged)
        except KeyboardInterrupt:
            return 130
        input("\n按 Enter 返回菜单...")
        return result.returncode

    def download(self, action: dict, log: Path) -> Path | None:
        url = action["url"]
        CACHE.mkdir(parents=True, exist_ok=True)
        target = CACHE / f"{safe_name(action['id'])}.sh"
        temp = CACHE / f".{target.name}.{os.getpid()}.tmp"
        if not shutil.which("curl"):
            print("缺少 curl，不能下载在线脚本。")
            return None
        result = self.run(["curl", "-fL", "--retry", "3", "--connect-timeout", "20", "--max-time", "1800", url, "-o", str(temp)], True)
        if result.returncode:
            print(result.stderr.strip() or "下载失败")
            temp.unlink(missing_ok=True)
            return None
        temp.chmod(0o700)
        temp.replace(target)
        with log.open("a", encoding="utf-8") as f:
            f.write(f"source: {url}\\ncache: {target}\\n")
            f.write(result.stderr)
        return target

    @staticmethod
    def syntax_ok(path: Path, interpreter: str) -> bool:
        checker = "sh" if interpreter == "sh" else "bash"
        result = subprocess.run([checker, "-n", str(path)], text=True, capture_output=True)
        if result.returncode:
            print("脚本语法检查失败，已停止执行：")
            print(result.stderr.strip())
            return False
        return True

    def run_fnm(self, installer: Path, log: Path) -> int:
        fnm_dir = Path.home() / ".local/share/fnm"
        fnm = fnm_dir / "fnm"
        bashrc = Path.home() / ".bashrc"
        block = "\n# >>> yjl-tui fnm >>>\nFNM_PATH=" + shlex.quote(str(fnm_dir)) + "\nif [ -d \"$FNM_PATH\" ]; then\n  export PATH=\"$FNM_PATH:$PATH\"\n  eval \"$(fnm env --shell bash)\"\nfi\n# <<< yjl-tui fnm <<<\n"
        old = bashrc.read_text(encoding="utf-8", errors="ignore") if bashrc.exists() else ""
        if "yjl-tui fnm" not in old:
            bashrc.parent.mkdir(parents=True, exist_ok=True)
            with bashrc.open("a", encoding="utf-8") as f:
                f.write(block)
        command = (
            f"bash {shlex.quote(str(installer))} --skip-shell --force-install --install-dir {shlex.quote(str(fnm_dir))}"
            f" && export PATH={shlex.quote(str(fnm_dir))}:$PATH"
            f" && eval \"$({shlex.quote(str(fnm))} env --shell bash)\""
            f" && {shlex.quote(str(fnm))} install 22.11.0"
            f" && {shlex.quote(str(fnm))} default 22.11.0"
            f" && {shlex.quote(str(fnm))} use 22.11.0 && node -v && npm -v"
        )
        return self.interactive(["bash", "-c", command], log)

    def online(self, action: dict, log: Path) -> int:
        path = self.download(action, log)
        if not path:
            return 1
        interpreter = action.get("interpreter", "bash")
        if not self.syntax_ok(path, interpreter):
            return 2
        if action.get("mode") == "fnm":
            return self.run_fnm(path, log)
        return self.interactive([interpreter, str(path), *map(str, action.get("args", []))], log)

    @staticmethod
    def builtin(action_id: str) -> list[str] | None:
        if action_id == "domain_latency":
            hosts = " ".join(shlex.quote(host) for host in DOMAIN_LATENCY_HOSTS)
            script = f'''set +e
for tool in timeout openssl date; do
  command -v "$tool" >/dev/null 2>&1 || {{ echo "缺少命令: $tool"; exit 1; }}
done
# Do not use date +%s%3N here: some Linux date implementations treat %3N
# as full nanoseconds, producing a 19-digit value and Bash arithmetic overflow.
now_ms() {{
  local stamp sec ns
  stamp=$(date +%s%N 2>/dev/null)
  if [[ "$stamp" =~ ^[0-9]{{19}}$ ]]; then
    printf '%s\\n' "$(printf '%s' "$stamp" | cut -c1-13)"
    return 0
  fi
  sec=$(date +%s)
  ns=$(date +%N 2>/dev/null)
  if [[ "$ns" =~ ^[0-9]{{9}}$ ]]; then
    printf '%s%03d\\n' "$sec" "$((10#$ns / 1000000))"
  else
    printf '%s000\\n' "$sec"
  fi
}}
for d in {hosts}; do
  t1=$(now_ms)
  if timeout 1 openssl s_client -connect "$d:443" -servername "$d" </dev/null >/dev/null 2>&1; then
    t2=$(now_ms)
    echo "$d: $((t2 - t1)) ms"
  else
    echo "$d: timeout"
  fi
done'''
            return ["bash", "-c", script]
        commands = {
            "system_info": ["bash", "-c", "echo '== OS =='; cat /etc/os-release 2>/dev/null || true; echo; echo '== Kernel =='; uname -a; echo; echo '== CPU =='; nproc 2>/dev/null || true; lscpu 2>/dev/null | sed -n '1,18p' || true; echo; echo '== Memory =='; free -h 2>/dev/null || true; echo; echo '== Disk =='; df -hT 2>/dev/null || true; echo; echo '== Virtualization =='; systemd-detect-virt 2>/dev/null || true"],
            "port_audit": ["bash", "-c", "echo '== Listening =='; ss -lntup 2>/dev/null || true; echo; echo '== SSH =='; (sshd -T 2>/dev/null || true) | grep -E '^(port|passwordauthentication|permitrootlogin) ' || true; echo; echo '== Services =='; systemctl --type=service --state=running --no-legend 2>/dev/null | sed -n '1,80p' || true"],
            "swap_check": ["bash", "-c", "swapon --show 2>/dev/null || true; echo; cat /proc/swaps 2>/dev/null || true; echo; free -h 2>/dev/null | sed -n '1,3p' || true"],
            "recent_logs": ["bash", "-c", f"find {shlex.quote(str(LOGS))} -type f -printf '%TY-%Tm-%Td %TH:%TM  %p\\n' 2>/dev/null | sort -r | sed -n '1,100p'"],
            "apt_upgrade": ["bash", "-c", "export DEBIAN_FRONTEND=noninteractive; apt-get update && apt-get upgrade -y"],
            "swap_builtin": ["bash", "-c", "set -Eeuo pipefail; if swapon --show --noheadings 2>/dev/null | grep -q .; then echo '[SKIP] 已存在活动 Swap，不修改。'; exit 0; fi; if [ ! -f /swapfile ]; then fallocate -l 512M /swapfile || dd if=/dev/zero of=/swapfile bs=1M count=512 status=progress; fi; chmod 600 /swapfile; mkswap /swapfile; swapon /swapfile; grep -q '^/swapfile ' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab; swapon --show"],
        }
        return commands.get(action_id)

    @staticmethod
    def listening(port: str) -> bool:
        result = subprocess.run(["ss", "-ltn"], text=True, capture_output=True)
        return bool(re.search(r"[:.]" + re.escape(port) + r"\s", result.stdout))

    def ssh_config(self, log: Path) -> int:
        if not IS_ROOT:
            print("SSH 配置需要 root 权限。")
            return 1
        user = input("目标用户 [root]: ").strip() or "root"
        port = input("SSH 新端口 [27272]: ").strip() or "27272"
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]*", user) or not port.isdigit() or not 1 <= int(port) <= 65535:
            print("用户名或端口格式不合法。")
            return 2
        if self.run(["id", user], True).returncode or not Path("/etc/ssh/sshd_config").is_file():
            print("目标用户不存在或 sshd_config 不存在。")
            return 1
        password = getpass.getpass("输入新密码（不会写入命令行或日志）：")
        if not password or self.listening(port):
            print("密码不能为空，或目标端口已被占用。")
            return 1
        config = Path("/etc/ssh/sshd_config")
        backup = log.with_suffix(".sshd_config.bak")
        shutil.copy2(config, backup)
        original = config.read_text(encoding="utf-8", errors="surrogateescape")
        updated = re.sub(r"(?im)^\s*#?\s*Port\s+\d+\s*$", "", original).rstrip()
        updated += f"\n\n# Managed by yjl-tui\nPort {port}\nPasswordAuthentication yes\n"
        if user == "root":
            updated += "PermitRootLogin yes\n"
        config.write_text(updated, encoding="utf-8", errors="surrogateescape")
        try:
            if shutil.which("sshd") and self.run(["sshd", "-t", "-f", str(config)], True).returncode:
                raise RuntimeError("sshd -t validation failed")
            result = self.run(["chpasswd"], True, f"{user}:{password}\n")
            if result.returncode:
                raise RuntimeError(result.stderr.strip() or "chpasswd failed")
            service = "ssh" if self.run(["systemctl", "list-unit-files", "ssh.service"], True).returncode == 0 else "sshd"
            result = self.run(["systemctl", "reload", service], True)
            if result.returncode:
                result = self.run(["systemctl", "restart", service], True)
            if result.returncode or not self.listening(port):
                raise RuntimeError(result.stderr.strip() or f"port {port} is not listening")
        except Exception as exc:
            shutil.copy2(backup, config)
            print(f"失败，已回滚 SSH 配置：{exc}")
            return 1
        print(f"密码已更新，SSH 已切换到 {port}。备份：{backup}")
        return 0

    def custom(self, log: Path) -> int:
        url = input("输入 http(s) URL：").strip()
        if not re.fullmatch(r"https?://[^\s]+", url, re.I):
            print("只允许 http(s) URL。")
            return 2
        return self.online({"id": "custom", "url": url, "interpreter": "bash", "args": []}, log)

    def execute(self, action: dict) -> None:
        log = self.log_file(action["id"])
        if not self.confirm(action):
            self.status = "已取消"
            return
        if action.get("needs_root") and not IS_ROOT:
            print("此动作需要 root 权限，请使用 sudo 或 root 登录后启动 TUI。")
            self.status = "权限不足，未执行"
            input("按 Enter 返回菜单...")
            return
        print("\n" + "=" * 70 + f"\n开始：{action['title']}\n" + "=" * 70)
        if action.get("kind") == "online":
            rc = self.online(action, log)
        elif action.get("kind") == "custom":
            rc = self.custom(log)
        elif action["id"] == "system_info":
            rc = self.system_info(log)
            input("\n按 Enter 返回菜单...")
        elif action["id"] == "apt_upgrade":
            rc = self.system_upgrade(log)
            input("\n按 Enter 返回菜单...")
        elif action["id"] == "log_manage":
            rc = self.log_manage(log)
            input("\n按 Enter 返回菜单...")
        elif action["id"] == "kernel_manage":
            rc = self.kernel_manage(log)
            input("\n按 Enter 返回菜单...")
        elif action["id"] == "ssl_manage":
            rc = self.ssl_manage(log)
            input("\n按 Enter 返回菜单...")
        elif action["id"] == "network_manage":
            rc = self.network_manage(log)
            input("\n按 Enter 返回菜单...")
        elif action["id"] == "grub_manage":
            rc = self.grub_manage(log)
            input("\n按 Enter 返回菜单...")
        elif action["id"] == "ip_preference":
            rc = self.ip_preference(log)
            input("\n按 Enter 返回菜单...")
        elif action["id"] == "webdav_manage":
            rc = self.webdav_manage(log)
            input("\n按 Enter 返回菜单...")
        elif action["id"] == "ssh_config":
            rc = self.ssh_config(log)
            input("\n按 Enter 返回菜单...")
        elif action["id"] == "tcp_status":
            rc = self.tcp_status(log)
            input("\n按 Enter 返回菜单...")
        elif action["id"] == "tcp_tune":
            rc = self.tcp_tune(log)
            input("\n按 Enter 返回菜单...")
        else:
            cmd = self.builtin(action["id"])
            rc = self.interactive(cmd, log) if cmd else 1
            if not cmd:
                print(f"尚未实现内置动作：{action['id']}")
        self.status = f"{action['title']} 结束，退出码 {rc}；日志：{log}"

    def draw(self, screen) -> None:
        screen.erase()
        height, width = screen.getmaxyx()
        screen.addnstr(0, 0, " YJL Linux TUI  ·  脚本终端管理工具 ", width - 1, curses.color_pair(2) | curses.A_BOLD)
        screen.addnstr(1, 0, f"工作区: {WORKSPACE} | 权限: {'root' if IS_ROOT else '普通用户'}", width - 1, curses.color_pair(3))
        left = max(22, min(30, width // 3))
        screen.vline(3, left, curses.ACS_VLINE, max(1, height - 7))
        screen.addnstr(3, 2, "分类", left - 4, curses.A_BOLD)
        for i, cat in enumerate(self.categories):
            attr = curses.color_pair(1) | curses.A_BOLD if i == self.category else 0
            screen.addnstr(5 + i, 2, ("❯ " if i == self.category else "  ") + cat["title"], left - 4, attr)
        actions = self.current_actions()
        screen.addnstr(3, left + 3, "动作（Enter 执行）", width - left - 5, curses.A_BOLD)
        for i, action in enumerate(actions):
            if 5 + i >= height - 6:
                break
            risk = action.get("risk", "warn")
            label = {"safe": "安全", "warn": "修改", "danger": "高危"}.get(risk, risk)
            attr = curses.color_pair(1) | curses.A_BOLD if i == self.selected else (curses.color_pair(4) if risk == "danger" else 0)
            screen.addnstr(5 + i, left + 3, ("❯ " if i == self.selected else "  ") + action["title"] + f" [{label}]", width - left - 5, attr)
        current = actions[self.selected] if actions else {"description": "此分类没有动作"}
        y = max(5, height - 5)
        screen.hline(y - 1, 0, curses.ACS_HLINE, width)
        screen.addnstr(y, 2, "说明：" + current.get("description", ""), width - 4, curses.color_pair(3))
        screen.addnstr(y + 1, 2, self.status, width - 4)
        screen.addnstr(height - 2, 2, "↑↓/jk 选择  ←→/Tab 分类  Enter 执行  q 退出", width - 4, curses.color_pair(3))
        screen.refresh()

    def run_ui(self, screen) -> None:
        curses.curs_set(0)
        curses.use_default_colors()
        curses.init_pair(1, curses.COLOR_BLACK, curses.COLOR_CYAN)
        curses.init_pair(2, curses.COLOR_CYAN, -1)
        curses.init_pair(3, curses.COLOR_YELLOW, -1)
        curses.init_pair(4, curses.COLOR_RED, -1)
        screen.keypad(True)
        while True:
            self.draw(screen)
            key = screen.getch()
            actions = self.current_actions()
            if key in (ord("q"), ord("Q")):
                return
            if key in (curses.KEY_LEFT, curses.KEY_BTAB, 9):
                self.category = (self.category - 1) % len(self.categories)
                self.selected = 0
            elif key == curses.KEY_RIGHT:
                self.category = (self.category + 1) % len(self.categories)
                self.selected = 0
            elif key in (curses.KEY_UP, ord("k")) and actions:
                self.selected = (self.selected - 1) % len(actions)
            elif key in (curses.KEY_DOWN, ord("j")) and actions:
                self.selected = (self.selected + 1) % len(actions)
            elif key in (curses.KEY_ENTER, 10, 13) and actions:
                curses.endwin()
                self.execute(actions[self.selected])
                screen.clear()


def main() -> int:
    if "--help" in sys.argv or "-h" in sys.argv:
        print("用法：tui.py [--list|--check]")
        return 0
    try:
        config = read_config()
    except Exception as exc:
        print(f"读取配置失败：{exc}", file=sys.stderr)
        return 1
    manager = TUI(config)
    if "--list" in sys.argv:
        for action in manager.actions:
            print(f"{action['id']:18} {action['title']} [{action.get('risk', 'warn')}]")
        return 0
    if "--check" in sys.argv:
        print(f"配置 OK: {CONFIG}")
        return 0
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        print("需要在真实终端运行；非交互检查请使用 --check。", file=sys.stderr)
        return 2
    terminal_notice()
    curses.wrapper(manager.run_ui)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
