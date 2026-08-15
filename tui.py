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

import kernel_manager
import nginx_manager
from nginx_manager import run_nginx_manager

APP_DIR = Path(__file__).resolve().parent
WORKSPACE = APP_DIR.parent
CONFIG = APP_DIR / "scripts.json"
TCP_PROFILES = APP_DIR / "tcp_profiles.json"
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


def command_output(command: list[str]) -> str:
    """Return command output without making optional probes fatal."""
    if not command or not shutil.which(command[0]):
        return ""
    try:
        result = subprocess.run(
            command,
            text=True,
            capture_output=True,
            check=False,
            env={**os.environ, "LC_ALL": "C"},
        )
    except OSError:
        return ""
    return result.stdout.strip()


def read_text_value(path: str) -> str:
    try:
        value = Path(path).read_text(encoding="utf-8", errors="ignore").strip()
    except OSError:
        return ""
    if value.lower() in {"", "none", "unknown", "not specified", "to be filled by o.e.m."}:
        return ""
    return value


def parse_lscpu(output: str) -> dict[str, str]:
    """Parse the stable ``label: value`` form emitted by lscpu."""
    values: dict[str, str] = {}
    for line in output.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key, value = key.strip(), value.strip()
        if key and value:
            values[key] = value
    return values


def read_cpuinfo_records() -> list[dict[str, str]]:
    try:
        source = Path("/proc/cpuinfo").read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return []
    records: list[dict[str, str]] = []
    for block in source.split("\n\n"):
        record: dict[str, str] = {}
        for line in block.splitlines():
            if ":" not in line:
                continue
            key, value = line.split(":", 1)
            if key.strip() and value.strip():
                record[key.strip()] = value.strip()
        if record:
            records.append(record)
    return records


def cpu_hardware_profile() -> dict[str, str]:
    """Collect CPU details visible to the current Linux guest or host."""
    lscpu = parse_lscpu(command_output(["lscpu"]))
    records = read_cpuinfo_records()
    first = records[0] if records else {}

    def value(*keys: str, default: str = "未知") -> str:
        for key in keys:
            candidate = lscpu.get(key) or first.get(key)
            if candidate:
                return candidate
        return default

    models = list(dict.fromkeys(record.get("model name", "") for record in records))
    models = [model for model in models if model]
    flags = value("Flags", "flags", default="")
    if lscpu.get("Virtualization"):
        cpu_virtualization = lscpu["Virtualization"]
    elif "vmx" in flags.split():
        cpu_virtualization = "Intel VT-x (vmx)"
    elif "svm" in flags.split():
        cpu_virtualization = "AMD-V (svm)"
    else:
        cpu_virtualization = "未识别"

    cache_parts = [
        f"{label}: {lscpu[label]}"
        for label in ("L1d cache", "L1i cache", "L2 cache", "L3 cache")
        if lscpu.get(label)
    ]
    cache = "; ".join(cache_parts) or value("cache size")
    frequency = value("CPU MHz", "cpu MHz", default="未提供")
    if frequency not in {"未知", "未提供"} and re.fullmatch(r"[0-9.]+", frequency):
        frequency = f"{frequency} MHz"

    return {
        "逻辑 CPU": value("CPU(s)", default=str(len(records) or "未知")),
        "CPU 架构": value("Architecture", default=command_output(["uname", "-m"]) or "未知"),
        "CPU 厂商": value("Vendor ID", "vendor_id"),
        "CPU 型号": "；".join(models) if models else value("Model name", "model name"),
        "CPU 家族": value("CPU family", "cpu family"),
        "型号编号": value("Model", "model"),
        "步进": value("Stepping", "stepping"),
        "插槽数": value("Socket(s)", default="未知"),
        "每插槽核心数": value("Core(s) per socket", "cpu cores"),
        "每核心线程数": value("Thread(s) per core", default="未知"),
        "当前频率": frequency,
        "缓存": cache,
        "地址宽度": value("Address sizes", default="未知"),
        "CPU 虚拟化支持": cpu_virtualization,
        "虚拟化厂商": value("Hypervisor vendor", default="未检测到"),
        "虚拟化类型": value("Virtualization type", default="未检测到"),
        "NUMA": value("NUMA node(s)", default="未知"),
        "指令集": flags or "未读取到",
    }


VIRTUALIZATION_LABELS = {
    "kvm": "KVM",
    "qemu": "QEMU",
    "vmware": "VMware",
    "microsoft": "Hyper-V",
    "microsoft-hyper-v": "Hyper-V",
    "oracle": "VirtualBox",
    "bhyve": "bhyve",
    "xen": "Xen",
    "lxc": "LXC",
    "lxc-libvirt": "LXC",
    "docker": "Docker",
    "podman": "Podman",
    "openvz": "OpenVZ",
    "systemd-nspawn": "systemd-nspawn",
    "none": "未检测到",
}


def virtualization_profile(cpu: dict[str, str] | None = None) -> dict[str, str]:
    """Identify VM/container type and expose DMI evidence when available."""
    detected = command_output(["systemd-detect-virt"]) or ""
    vm = command_output(["systemd-detect-virt", "--vm"]) or "none"
    container = command_output(["systemd-detect-virt", "--container"]) or "none"
    detected_label = VIRTUALIZATION_LABELS.get(detected.lower(), detected or "未知")
    vm_label = VIRTUALIZATION_LABELS.get(vm.lower(), vm)
    container_label = VIRTUALIZATION_LABELS.get(container.lower(), container)
    if vm != "none":
        running_mode = "虚拟机"
    elif container != "none":
        running_mode = "容器"
    else:
        running_mode = "物理机或未识别"

    cpu = cpu or {}
    hypervisor = cpu.get("虚拟化厂商", "未检测到")
    if detected in {"", "none"} and hypervisor != "未检测到":
        detected_label = hypervisor
    if running_mode == "虚拟机":
        host_cpu = "虚拟机内不可直接读取；下方为宿主机暴露给本机的 vCPU"
    elif running_mode == "容器":
        host_cpu = "容器与宿主机共享 CPU；下方为当前系统可见 CPU"
    else:
        host_cpu = "当前系统可见 CPU（未确认存在虚拟化层）"

    return {
        "虚拟化环境": detected_label,
        "运行形态": running_mode,
        "虚拟机检测": vm_label,
        "容器检测": container_label,
        "Hypervisor 厂商": hypervisor,
        "DMI 系统厂商": read_text_value("/sys/class/dmi/id/sys_vendor") or "未提供",
        "DMI 产品型号": read_text_value("/sys/class/dmi/id/product_name") or "未提供",
        "DMI 产品版本": read_text_value("/sys/class/dmi/id/product_version") or "未提供",
        "DMI 主板型号": read_text_value("/sys/class/dmi/id/board_name") or "未提供",
        "内核虚拟化标记": "是（hypervisor）" if "hypervisor" in cpu.get("指令集", "").split() else "未发现",
        "宿主机 CPU 读取": host_cpu,
    }


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
    virt = virtualization_profile()
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
        "虚拟化": virt["虚拟化环境"],
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
        self.should_exit = False
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
        cpu = cpu_hardware_profile()
        print("\n== CPU 硬件详情（当前系统可见） ==")
        for key, value in cpu.items():
            print(f"{key}: {value}")
        print("\n== 虚拟化详情 ==")
        for key, value in virtualization_profile(cpu).items():
            print(f"{key}: {value}")
        print("\n== 内存 ==")
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

    def system_kernel_maintenance(self, log: Path) -> int:
        if not self.root_required():
            return 1
        facts = kernel_manager.collect_kernel_facts()
        print(kernel_manager.format_kernel_report(facts))
        if reason := facts.installation_block_reason():
            print(f"\n{reason}")
            return 1
        if not shutil.which("apt-cache") or not shutil.which("apt-get"):
            print("\n当前系统没有 apt，暂不能使用系统内核维护。")
            return 1
        print("\n1. 查看官方稳定内核安装计划\n2. 安装官方稳定内核\n3. 查看 Ubuntu HWE 计划\n4. 查看 Ubuntu Mainline 说明\n5. 查看已安装内核与 GRUB 状态")
        choice = input("选择 [1]: ").strip() or "1"
        track = "hwe" if choice == "3" else "stable"
        identity = facts.identity
        if choice in {"1", "2", "3"}:
            packages = (
                (f"linux-image-{identity.architecture}", f"linux-headers-{identity.architecture}")
                if identity.distro_id == "debian"
                else ((f"linux-generic-hwe-{identity.version_id}", f"linux-headers-generic-hwe-{identity.version_id}")
                      if track == "hwe" else ("linux-generic", "linux-headers-generic"))
            )
            candidates = {}
            for package in packages:
                value = command_output(["apt-cache", "policy", package])
                candidate = re.search(r"(?m)^\s*Candidate:\s*(\S+)", value)
                if candidate and candidate.group(1) != "(none)":
                    candidates[package] = candidate.group(1)
            plan = kernel_manager.recommended_apt_plan(identity, candidates, track)
            if not plan:
                print("当前已配置 apt 源没有完整的对应内核元包，不执行安装。")
                return 1
            print(f"\n计划：{plan.label}\n来源：当前已配置的 apt 源\n包：{' '.join(plan.packages)}")
            if choice != "2":
                return 0
            if input("输入 INSTALL 确认仅安装以上内核包：").strip() != "INSTALL":
                print("已取消。")
                return 2
            result = subprocess.run(["apt-get", "install", "-y", *plan.packages])
            if result.returncode:
                return result.returncode
            if shutil.which("update-initramfs"):
                subprocess.run(["update-initramfs", "-u"], check=False)
            if shutil.which("update-grub"):
                subprocess.run(["update-grub"], check=False)
            print("内核包已安装并已尝试刷新引导。请进入“引导维护”确认新内核，再重启并用 uname -r 验证。")
            return 0
        if choice == "4":
            print("Ubuntu Mainline 预编译包仅在 amd64 且上游页面显示成功、包完整、校验通过时才会提供；arm64 请使用官方 apt 或本地源码编译。")
            return 0
        if choice == "5":
            subprocess.run(["dpkg", "-l", "linux-image*"], check=False)
            subprocess.run(["bash", "-c", "grep -E '^menuentry|^submenu' /boot/grub/grub.cfg 2>/dev/null || true"])
            return 0
        return 2

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
        for command in (
            ("uname", "-r"),
            ("sysctl", "-n", "net.ipv4.tcp_congestion_control"),
            ("sysctl", "-n", "net.core.default_qdisc"),
            ("sysctl", "-n", "net.ipv4.tcp_fastopen"),
            ("sysctl", "-n", "net.ipv4.tcp_ecn"),
            ("sysctl", "-n", "net.ipv6.conf.all.disable_ipv6"),
        ):
            if shutil.which(command[0]):
                result = subprocess.run(list(command), text=True, capture_output=True)
                print(f"{' '.join(command)}: {result.stdout.strip() or result.stderr.strip()}")
        for name in ("tcp_available_congestion_control", "tcp_allowed_congestion_control"):
            available = Path("/proc/sys/net/ipv4") / name
            if available.is_file():
                print(f"{name}：{available.read_text(encoding='utf-8', errors='ignore').strip()}")
        if shutil.which("lsmod"):
            result = subprocess.run(["lsmod"], text=True, capture_output=True)
            bbr_modules = [line for line in result.stdout.splitlines() if "bbr" in line.lower()]
            print("BBR 模块：" + ("\n".join(bbr_modules) if bbr_modules else "未列出（多数较新内核将 BBR 内建；以可用算法中含 bbr 为准）"))
        if shutil.which("tc"):
            print("\n== qdisc ==")
            subprocess.run(["tc", "qdisc", "show"])
        return 0

    @staticmethod
    def tcp_profile_data() -> dict:
        try:
            with TCP_PROFILES.open(encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, json.JSONDecodeError) as exc:
            print(f"读取 TCP 配置方案失败：{exc}")
            return {}
        if not isinstance(data, dict):
            print("TCP 配置方案格式错误：根节点必须是对象。")
            return {}
        return data

    @staticmethod
    def tcp_parse_settings(path: Path) -> dict[str, str]:
        settings: dict[str, str] = {}
        if not path.is_file():
            return settings
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, value = stripped.split("=", 1)
            key = key.strip()
            value = value.strip()
            if re.fullmatch(r"[A-Za-z0-9_.]+", key) and value:
                settings[key] = value
        return settings

    def tcp_dynamic_settings(self, settings: dict[str, str]) -> dict[str, str] | None:
        memory_match = re.search(r"MemTotal:\s+(\d+)", Path("/proc/meminfo").read_text(encoding="utf-8", errors="ignore")) if Path("/proc/meminfo").is_file() else None
        default_memory = max(1, int(memory_match.group(1)) // 1024) if memory_match else 1024
        prompts = (
            ("网络延迟(ms)", "100"),
            ("本地带宽(Mbps)", "1000"),
            ("VPS带宽(Mbps)", "1000"),
            ("VPS内存(MB)", str(default_memory)),
        )
        values: list[int] = []
        print("激进方案会按 BDP 计算缓冲区。直接回车使用默认值，不会自动重启。")
        for label, default in prompts:
            raw = input(f"{label} [{default}]: ").strip() or default
            if not raw.isdigit() or int(raw) <= 0:
                print(f"{label} 必须是正整数，已取消。")
                return None
            values.append(int(raw))
        latency, local_bw, vps_bw, vps_mem = values
        min_bw = min(local_bw, vps_bw)
        bdp = min_bw * 1_000_000 * latency // 8 // 1000
        max_mem_bytes = vps_mem * 1024 * 1024 * 50 // 100
        rmem_max = min(max(bdp * 2, 1_048_576), max_mem_bytes)
        wmem_max = min(max(bdp * 3 // 2, 1_048_576), max_mem_bytes)
        netdev_backlog = min(max(min_bw * 10, 1000), 10000)
        somaxconn = min(max(vps_mem * 20, 512), 16384)
        syn_backlog = min(somaxconn * 4, 65536)
        init_cwnd = min(max(latency // 20 + 10, 10), 32)
        min_free = min(max(vps_mem * 1024 * 12 // 100, 65536), 524288)
        settings.update({
            "vm.min_free_kbytes": str(min_free),
            "net.core.netdev_max_backlog": str(netdev_backlog),
            "net.core.rmem_max": str(rmem_max),
            "net.core.wmem_max": str(wmem_max),
            "net.core.somaxconn": str(somaxconn),
            "net.ipv4.tcp_rmem": f"32768 262144 {rmem_max}",
            "net.ipv4.tcp_wmem": f"32768 262144 {wmem_max}",
            "net.ipv4.tcp_init_cwnd": str(init_cwnd),
            "net.ipv4.tcp_max_syn_backlog": str(syn_backlog),
        })
        print(f"最终参数：延迟={latency}ms，本地带宽={local_bw}Mbps，VPS带宽={vps_bw}Mbps，内存={vps_mem}MB")
        return settings

    def tcp_apply_profile(self, profile_id: str, log: Path) -> int:
        if not self.root_required():
            return 1
        profiles = self.tcp_profile_data()
        profile = profiles.get(profile_id)
        if not isinstance(profile, dict) or not isinstance(profile.get("settings"), dict):
            print(f"没有找到 TCP 配置方案：{profile_id}")
            return 1
        settings = self.tcp_parse_settings(Path("/etc/sysctl.d/99-yjl-tcp-tuning.conf"))
        for key, value in profile["settings"].items():
            if not re.fullmatch(r"[A-Za-z0-9_.]+", str(key)) or not re.fullmatch(r"[^#\n]+", str(value).strip()):
                print(f"配置方案包含不合法参数：{key}={value}")
                return 2
            settings[str(key)] = str(value).strip()
        if profile.get("dynamic") and self.tcp_dynamic_settings(settings) is None:
            return 2
        if profile.get("settings", {}).get("net.ipv4.tcp_congestion_control"):
            requested = str(profile["settings"]["net.ipv4.tcp_congestion_control"])
            allowed_path = Path("/proc/sys/net/ipv4/tcp_allowed_congestion_control")
            allowed = allowed_path.read_text(encoding="utf-8", errors="ignore").split() if allowed_path.is_file() else []
            if allowed and requested not in allowed:
                print(f"当前内核不支持 {requested}，可用：{' '.join(allowed)}")
                print("请先使用带 (fsc) 的内核安装入口，或选择当前内核支持的方案。")
                return 2
        target = Path("/etc/sysctl.d/99-yjl-tcp-tuning.conf")
        target.parent.mkdir(parents=True, exist_ok=True)
        backup = self.backup_file(target)
        lines = [f"{key} = {value}" for key, value in settings.items()]
        target.write_text(
            "# Managed by dandan-tui; profile: " + profile_id + "\n" + "\n".join(lines) + "\n",
            encoding="utf-8",
        )
        print(f"已写入：{target}" + (f"；备份：{backup}" if backup else ""))
        if profile.get("limits"):
            limits = Path("/etc/security/limits.d/99-yjl-tui-tcp.conf")
            limits.parent.mkdir(parents=True, exist_ok=True)
            limits_backup = self.backup_file(limits)
            limits.write_text(
                "# Managed by dandan-tui\n* soft nofile 1000000\n* hard nofile 1000000\n",
                encoding="utf-8",
            )
            print(f"已写入：{limits}" + (f"；备份：{limits_backup}" if limits_backup else ""))
            if system_profile()["初始化"] == "systemd":
                dropin = Path("/etc/systemd/system.conf.d/99-yjl-tui-tcp.conf")
                dropin.parent.mkdir(parents=True, exist_ok=True)
                dropin_backup = self.backup_file(dropin)
                dropin.write_text(
                    "# Managed by dandan-tui\n[Manager]\nDefaultLimitNOFILE=1000000\n",
                    encoding="utf-8",
                )
                print(f"已写入：{dropin}" + (f"；备份：{dropin_backup}" if dropin_backup else ""))
                if shutil.which("systemctl"):
                    subprocess.run(["systemctl", "daemon-reload"], check=False)
        if not shutil.which("sysctl"):
            print("缺少 sysctl，参数已保存但没有应用。")
            return 1
        result = subprocess.run(["sysctl", "--system"])
        print("\n应用后的关键状态：")
        self.tcp_status(log)
        if result.returncode:
            print("部分参数可能不受当前内核支持；已保留配置文件，请按输出修正或改用兼容方案。")
        else:
            print(f"本地方案已应用：{profile.get('title', profile_id)}；无需联网安装。")
        return result.returncode

    @staticmethod
    def restore_tcp_file(path: Path) -> None:
        backups = sorted(path.parent.glob(path.name + ".yjl-tui.bak.*"))
        if backups:
            shutil.copy2(backups[0], path)
            print(f"已恢复原文件：{path} <- {backups[0]}")
        elif path.exists():
            path.unlink()
            print(f"已删除 TUI 文件：{path}")

    def tcp_remove_all(self, log: Path) -> int:
        if not self.root_required():
            return 1
        paths = (
            Path("/etc/sysctl.d/99-yjl-tcp-tuning.conf"),
            Path("/etc/security/limits.d/99-yjl-tui-tcp.conf"),
            Path("/etc/systemd/system.conf.d/99-yjl-tui-tcp.conf"),
        )
        for path in paths:
            self.restore_tcp_file(path)
        if shutil.which("systemctl") and Path("/run/systemd/system").exists():
            subprocess.run(["systemctl", "daemon-reload"], check=False)
        if shutil.which("sysctl"):
            result = subprocess.run(["sysctl", "--system"])
        else:
            result = subprocess.CompletedProcess([], 1)
        print("已卸载本 TUI 写入的 TCP 加速配置；没有删除其他软件的 sysctl 文件。")
        return result.returncode

    def confirm(self, action: dict) -> bool:
        return True

    def interactive(
        self,
        cmd: list[str],
        log: Path,
        env: dict[str, str] | None = None,
        cwd: Path | None = None,
        pause: bool = True,
    ) -> int:
        print(f"\n命令：{shlex.join(cmd)}\n日志：{log}")
        print("以下进入原脚本的真实终端交互，结束后按 Enter 返回。\n")
        merged = os.environ.copy()
        if env:
            merged.update(env)
        run_cmd = ["script", "-qefc", shlex.join(cmd), str(log)] if shutil.which("script") else cmd
        try:
            result = subprocess.run(run_cmd, env=merged, cwd=str(cwd) if cwd else None)
        except KeyboardInterrupt:
            return 130
        if pause:
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
        if interpreter in {"python", "python3"}:
            checker = shutil.which(interpreter)
            if not checker:
                print(f"缺少 {interpreter}，已停止执行。")
                return False
            check_code = (
                "import ast, pathlib, sys; "
                "ast.parse(pathlib.Path(sys.argv[1]).read_text(encoding='utf-8'), filename=sys.argv[1])"
            )
            result = subprocess.run([checker, "-c", check_code, str(path)], text=True, capture_output=True)
        else:
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
        env = action.get("env") if isinstance(action.get("env"), dict) else None
        return self.interactive([interpreter, str(path), *map(str, action.get("args", []))], log, env)

    def local_script(self, action: dict, log: Path) -> int:
        relative = action.get("path")
        if not isinstance(relative, str) or not relative:
            print("本地脚本路径未配置。")
            return 2
        path = (APP_DIR / relative).resolve()
        try:
            path.relative_to(APP_DIR.resolve())
        except ValueError:
            print("本地脚本路径超出 TUI 工作目录，已停止执行。")
            return 2
        if not path.is_file():
            print(f"未找到本地脚本：{path}")
            return 1
        interpreter = action.get("interpreter", "bash")
        if not self.syntax_ok(path, interpreter):
            return 2
        return self.interactive([interpreter, str(path), *map(str, action.get("args", []))], log)

    @staticmethod
    def _tcp_brutal_matching_brace(text: str, start: int) -> int | None:
        if start >= len(text) or text[start] != "{":
            return None
        depth = 0
        in_string = False
        escaped = False
        line_comment = False
        block_comment = False
        index = start
        while index < len(text):
            char = text[index]
            next_char = text[index + 1] if index + 1 < len(text) else ""
            if line_comment:
                if char == "\n":
                    line_comment = False
                index += 1
                continue
            if block_comment:
                if char == "*" and next_char == "/":
                    block_comment = False
                    index += 2
                else:
                    index += 1
                continue
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
                index += 1
                continue
            if char == '"':
                in_string = True
            elif char == "/" and next_char == "/":
                line_comment = True
                index += 2
                continue
            elif char == "/" and next_char == "*":
                block_comment = True
                index += 2
                continue
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    return index
            index += 1
        return None

    @classmethod
    def _tcp_brutal_patch_multiplex_block(cls, block: str) -> tuple[str, bool]:
        updated = block
        changed = False
        brutal_match = re.search(r'(?m)^\s*"brutal"\s*:\s*\{', updated)
        direct_region_end = brutal_match.start() if brutal_match else len(updated)
        direct_region = updated[:direct_region_end]
        direct_enabled = re.search(r'("enabled"\s*:\s*)(?:true|false)', direct_region)
        if direct_enabled and direct_enabled.group(0).endswith("false"):
            start, end = direct_enabled.span()
            direct_region = direct_region[:start] + direct_enabled.group(1) + "true" + direct_region[end:]
            updated = direct_region + updated[direct_region_end:]
            changed = True

        brutal_match = re.search(r'(?m)\s*"brutal"\s*:\s*\{', updated)
        if brutal_match:
            brutal_start = updated.find("{", brutal_match.start())
            brutal_end = cls._tcp_brutal_matching_brace(updated, brutal_start)
            if brutal_end is not None:
                brutal_block = updated[brutal_start:brutal_end + 1]
                brutal_enabled = re.search(r'("enabled"\s*:\s*)(?:true|false)', brutal_block)
                if brutal_enabled:
                    if brutal_enabled.group(0).endswith("false"):
                        start, end = brutal_enabled.span()
                        brutal_block = brutal_block[:start] + brutal_enabled.group(1) + "true" + brutal_block[end:]
                        changed = True
                else:
                    brutal_block = brutal_block[:-1] + '\n                    "enabled": true\n                }'
                    changed = True
                updated = updated[:brutal_start] + brutal_block + updated[brutal_end + 1:]
        else:
            closing = updated.rfind("}")
            if closing > 0:
                prefix = updated[:closing].rstrip()
                separator = "" if prefix.endswith(("{", ",")) else ","
                addition = (
                    f'{separator}\n                "brutal": {{\n'
                    '                    "enabled": true,\n'
                    '                    "up_mbps": 1000,\n'
                    '                    "down_mbps": 1000\n'
                    '                }\n            '
                )
                updated = prefix + addition + updated[closing:]
                changed = True
        return updated, changed

    @classmethod
    def _tcp_brutal_patch_jsonc_inbound(cls, text: str) -> tuple[str, bool]:
        pattern = re.compile(r'"multiplex"\s*:\s*\{')
        cursor = 0
        pieces: list[str] = []
        changed = False
        found = False
        while True:
            match = pattern.search(text, cursor)
            if not match:
                pieces.append(text[cursor:])
                break
            opening = text.find("{", match.start())
            closing = cls._tcp_brutal_matching_brace(text, opening)
            if closing is None:
                pieces.append(text[cursor:])
                break
            found = True
            pieces.append(text[cursor:opening])
            block, block_changed = cls._tcp_brutal_patch_multiplex_block(text[opening:closing + 1])
            pieces.append(block)
            changed = changed or block_changed
            cursor = closing + 1
        updated = "".join(pieces)
        if found:
            return updated, changed

        inbounds = re.search(r'"inbounds"\s*:\s*\[', updated)
        if not inbounds:
            return text, False
        opening = updated.find("{", inbounds.end())
        closing = cls._tcp_brutal_matching_brace(updated, opening)
        if opening < 0 or closing is None:
            return text, False
        prefix = updated[:closing].rstrip()
        separator = "" if prefix.endswith(("{", ",")) else ","
        addition = (
            f'{separator}\n            "multiplex": {{\n'
            '                "enabled": true,\n'
            '                "padding": true,\n'
            '                "brutal": {\n'
            '                    "enabled": true,\n'
            '                    "up_mbps": 1000,\n'
            '                    "down_mbps": 1000\n'
            '                }\n'
            '            }\n        '
        )
        return prefix + addition + updated[closing:], True

    @staticmethod
    def _tcp_brutal_supported_node(node: dict) -> bool:
        node_type = str(node.get("type", "")).lower()
        if node_type in {"shadowtls", "shadowsocks", "trojan"}:
            return True
        if node_type == "vmess":
            network = node.get("network") or (node.get("transport") or {}).get("type")
            return str(network).lower() == "ws"
        if node_type == "vless":
            if str(node.get("flow", "")).lower() == "xtls-rprx-vision":
                return False
            network = node.get("network") or (node.get("transport") or {}).get("type")
            return str(network).lower() in {"ws", "http", "grpc"}
        return False

    @classmethod
    def _tcp_brutal_patch_singbox_subscription(
        cls, text: str, target_tags: set[str]
    ) -> tuple[str, bool]:
        source = re.sub(r'(?m)^\s*//.*\n', "", text)
        source = re.sub(r",\s*([}\]])", r"\1", source)
        try:
            data = json.loads(source)
        except json.JSONDecodeError:
            return text, False
        changed = False

        def visit(value):
            nonlocal changed
            if isinstance(value, dict):
                tag = str(value.get("tag", ""))
                if tag in target_tags and cls._tcp_brutal_supported_node(value):
                    multiplex = value.setdefault("multiplex", {})
                    if not isinstance(multiplex, dict):
                        multiplex = {}
                        value["multiplex"] = multiplex
                    if multiplex.get("enabled") is not True:
                        multiplex["enabled"] = True
                        changed = True
                    brutal = multiplex.setdefault("brutal", {})
                    if not isinstance(brutal, dict):
                        brutal = {}
                        multiplex["brutal"] = brutal
                    if brutal.get("enabled") is not True:
                        brutal["enabled"] = True
                        changed = True
                    brutal.setdefault("up_mbps", 1000)
                    brutal.setdefault("down_mbps", 1000)
                for child in value.values():
                    visit(child)
            elif isinstance(value, list):
                for child in value:
                    visit(child)

        visit(data)
        if not changed:
            return text, False
        return json.dumps(data, ensure_ascii=False, indent=2) + "\n", True

    @staticmethod
    def _tcp_brutal_yaml_supported(block: str, target_tags: set[str]) -> bool:
        if not any(tag and tag in block for tag in target_tags):
            return False
        node_type = re.search(r'\btype:\s*([A-Za-z0-9_-]+)', block, re.I)
        if not node_type:
            return False
        node_type = node_type.group(1).lower()
        if node_type in {"shadowtls", "ss", "trojan"}:
            return True
        network = re.search(r'\bnetwork:\s*([A-Za-z0-9_-]+)', block, re.I)
        flow = re.search(r'\bflow:\s*([^,\s}]+)', block, re.I)
        if node_type == "vmess":
            return bool(network and network.group(1).lower() == "ws")
        if node_type == "vless":
            return bool(
                network
                and network.group(1).lower() in {"ws", "http", "grpc"}
                and not (flow and flow.group(1).lower() == "xtls-rprx-vision")
            )
        return False

    @staticmethod
    def _tcp_brutal_yaml_set_nested_enabled(block: str, key: str) -> tuple[str, bool]:
        inline = re.compile(rf'({re.escape(key)}\s*:\s*\{{\s*enabled\s*:\s*)(false|true)', re.I)
        updated, count = inline.subn(r"\1true", block, count=1)
        changed = count > 0 and updated != block
        lines = updated.splitlines(keepends=True)
        for index, line in enumerate(lines):
            match = re.match(rf'^(\s*){re.escape(key)}\s*:\s*$', line, re.I)
            if not match:
                continue
            key_indent = len(match.group(1).replace("\t", "    "))
            for child in range(index + 1, len(lines)):
                stripped = lines[child].strip()
                if not stripped:
                    continue
                child_indent = len(lines[child]) - len(lines[child].lstrip(" \t"))
                if child_indent <= key_indent:
                    break
                enabled = re.match(r'^(\s*enabled\s*:\s*)(false|true)(\s*)$', lines[child], re.I)
                if enabled and enabled.group(2).lower() == "false":
                    lines[child] = enabled.group(1) + "true" + enabled.group(3) + ("\n" if lines[child].endswith("\n") else "")
                    changed = True
                    break
        return "".join(lines), changed

    @classmethod
    def _tcp_brutal_patch_yaml_block(cls, block: str) -> tuple[str, bool]:
        updated, smux_changed = cls._tcp_brutal_yaml_set_nested_enabled(block, "smux")
        updated, brutal_changed = cls._tcp_brutal_yaml_set_nested_enabled(updated, "brutal-opts")
        changed = smux_changed or brutal_changed
        has_smux = re.search(r'\bsmux\s*:', updated, re.I) is not None
        has_brutal = re.search(r'\bbrutal-opts\s*:', updated, re.I) is not None
        if has_smux and not has_brutal:
            if "\n" not in updated:
                closing = updated.rfind("}")
                if closing > 0:
                    updated = (
                        updated[:closing].rstrip()
                        + ", brutal-opts: { enabled: true, up: 1000 Mbps, down: 1000 Mbps }"
                        + updated[closing:]
                    )
                    changed = True
            else:
                newline = "\r\n" if "\r\n" in updated else "\n"
                base = re.search(r'(?m)^(\s*)type:', updated)
                indent = base.group(1) if base else "  "
                core = updated.rstrip("\r\n")
                updated = core + newline + (
                    f"{indent}brutal-opts:{newline}"
                    f"{indent}  enabled: true{newline}"
                    f"{indent}  up: 1000 Mbps{newline}"
                    f"{indent}  down: 1000 Mbps{newline}"
                )
                changed = True
        elif not has_smux:
            newline = "\r\n" if "\r\n" in updated else "\n"
            base = re.search(r'(?m)^(\s*)type:', updated)
            indent = base.group(1) if base else "  "
            if "\n" not in updated:
                closing = updated.rfind("}")
                if closing > 0:
                    updated = (
                        updated[:closing].rstrip()
                        + ", smux: { enabled: true, protocol: h2mux, padding: true },"
                        + " brutal-opts: { enabled: true, up: 1000 Mbps, down: 1000 Mbps }"
                        + updated[closing:]
                    )
                    changed = True
            else:
                core = updated.rstrip("\r\n")
                updated = core + newline + (
                    f"{indent}smux:{newline}"
                    f"{indent}  enabled: true{newline}"
                    f"{indent}  protocol: h2mux{newline}"
                    f"{indent}  padding: true{newline}"
                    f"{indent}brutal-opts:{newline}"
                    f"{indent}  enabled: true{newline}"
                    f"{indent}  up: 1000 Mbps{newline}"
                    f"{indent}  down: 1000 Mbps{newline}"
                )
                changed = True
        return updated, changed

    @classmethod
    def _tcp_brutal_patch_yaml_subscription(
        cls, text: str, target_tags: set[str]
    ) -> tuple[str, bool]:
        marker = re.compile(r'(?m)^[ \t]*- (?=(?:name:|\{name:))')
        matches = list(marker.finditer(text))
        if not matches:
            return text, False
        pieces: list[str] = []
        changed = False
        cursor = 0
        for index, match in enumerate(matches):
            end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
            first_line_end = text.find("\n", match.end())
            first_line_end = len(text) if first_line_end < 0 else first_line_end + 1
            root_section = re.search(r'(?m)^[^\s#-][^\r\n]*$', text[first_line_end:])
            if root_section:
                end = min(end, first_line_end + root_section.start())
            pieces.append(text[cursor:match.start()])
            block = text[match.start():end]
            if cls._tcp_brutal_yaml_supported(block, target_tags):
                block, block_changed = cls._tcp_brutal_patch_yaml_block(block)
                changed = changed or block_changed
            pieces.append(block)
            cursor = end
        if not pieces:
            return text, False
        pieces.append(text[cursor:])
        return "".join(pieces), changed

    @staticmethod
    def _tcp_brutal_atomic_write(path: Path, text: str) -> None:
        temp = path.with_name(f".{path.name}.yjl-tcp-brutal.{os.getpid()}.tmp")
        try:
            temp.write_text(text, encoding="utf-8", errors="surrogateescape")
            shutil.copymode(path, temp)
            os.replace(temp, path)
        finally:
            temp.unlink(missing_ok=True)

    def tcp_brutal_repair(self, log: Path) -> int:
        if not Path("/sys/module/brutal").exists():
            print("当前内核模块 brutal 没有加载，先安装或加载 TCP Brutal，未修改 sing-box 文件。")
            return 1
        work_dir = Path("/etc/sing-box")
        conf_dir = work_dir / "conf"
        subscribe_dir = work_dir / "subscribe"
        if not conf_dir.is_dir():
            print(f"未找到 sing-box 配置目录：{conf_dir}")
            return 1

        inbound_names = {
            "14_ShadowTLS_inbounds.json",
            "15_shadowsocks_inbounds.json",
            "16_trojan_inbounds.json",
            "17_vmess-ws_inbounds.json",
            "18_vless-ws-tls_inbounds.json",
            "19_h2-reality_inbounds.json",
            "20_grpc-reality_inbounds.json",
        }
        target_tags: set[str] = set()
        file_updates: dict[Path, str] = {}
        for path in sorted(conf_dir.glob("*_inbounds.json")):
            if path.name not in inbound_names:
                continue
            original = path.read_text(encoding="utf-8", errors="surrogateescape")
            for tag in re.findall(r'"tag"\s*:\s*"([^"]+)"', original):
                target_tags.add(tag)
            updated, changed = self._tcp_brutal_patch_jsonc_inbound(original)
            if changed:
                file_updates[path] = updated

        if not target_tags:
            print("没有从支持的 sing-box 入站配置中找到节点 tag，未修改订阅。")
            return 1

        if subscribe_dir.is_dir():
            for path in sorted(subscribe_dir.iterdir()):
                if not path.is_file() or path.name == "qr":
                    continue
                original = path.read_text(encoding="utf-8", errors="surrogateescape")
                if path.name == "sing-box":
                    updated, changed = self._tcp_brutal_patch_singbox_subscription(original, target_tags)
                elif path.name in {"proxies", "clash", "clash2", "clash3"}:
                    updated, changed = self._tcp_brutal_patch_yaml_subscription(original, target_tags)
                else:
                    continue
                if changed:
                    file_updates[path] = updated

        if not file_updates:
            print("已有支持协议的配置和结构化订阅均已启用 TCP Brutal，没有需要修改的文件。")
            return 0

        backup_dir = work_dir / f"tcp-brutal-backup-{time.strftime('%Y%m%d-%H%M%S')}"
        backups: dict[Path, Path] = {}
        try:
            for path, updated in file_updates.items():
                relative = path.relative_to(work_dir)
                backup = backup_dir / relative
                backup.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, backup)
                backups[path] = backup
                self._tcp_brutal_atomic_write(path, updated)
            print(f"已更新 {len(file_updates)} 个文件；备份：{backup_dir}")
        except OSError as exc:
            print(f"写入 TCP Brutal 配置失败：{exc}")
            for path, backup in backups.items():
                shutil.copy2(backup, path)
            return 1

        binary = next((candidate for candidate in (
            Path("/etc/sing-box/sing-box"), Path("/usr/local/bin/sing-box"), Path("/usr/bin/sing-box")
        ) if candidate.is_file() and os.access(candidate, os.X_OK)), None)
        if binary:
            check = subprocess.run([str(binary), "check", "-C", str(conf_dir)], text=True, capture_output=True)
            if check.returncode:
                print("sing-box 配置检查失败，正在恢复本次修改。")
                for path, backup in backups.items():
                    shutil.copy2(backup, path)
                print(check.stderr.strip() or check.stdout.strip())
                return check.returncode

        conf_changed = any(path.parent == conf_dir for path in file_updates)
        if conf_changed:
            if shutil.which("systemctl") and Path("/run/systemd/system").exists():
                reload_result = subprocess.run(["systemctl", "reload", "sing-box"], check=False)
            elif shutil.which("rc-service"):
                reload_result = subprocess.run(["rc-service", "sing-box", "restart"], check=False)
            else:
                reload_result = subprocess.CompletedProcess([], 1)
            if reload_result.returncode:
                print("配置文件已写入，但 sing-box 热加载失败；备份仍保留，请检查服务日志。")
                return reload_result.returncode
            print("sing-box 已重新加载 TCP Brutal 配置。")
        print("TCP Brutal 配置和结构化订阅修复完成。")
        return 0

    def tcp_online(self, action: dict, log: Path) -> int:
        path = self.download(action, log)
        if not path:
            return 1
        interpreter = action.get("interpreter", "bash")
        if not self.syntax_ok(path, interpreter):
            return 2
        choice = str(action.get("tcp_choice", "")).strip()
        if not choice:
            return self.interactive([interpreter, str(path)], log)
        source = path.read_text(encoding="utf-8", errors="ignore")
        marker = '  read -p " 请输入数字 :" num'
        replacement = (
            '  if [[ -n "${YJL_TUI_TCP_CHOICE:-}" ]]; then\n'
            '    num="${YJL_TUI_TCP_CHOICE}"\n'
            '    unset YJL_TUI_TCP_CHOICE\n'
            '  else\n'
            '    read -p " 请输入数字 :" num\n'
            '  fi'
        )
        if marker not in source:
            print("上游 tcp.sh 菜单格式已变化，无法自动定位编号；将打开原始菜单。")
            return self.interactive([interpreter, str(path)], log)
        selected = path.with_name(f".{path.name}.{safe_name(choice)}.selected")
        selected.write_text(source.replace(marker, replacement, 1), encoding="utf-8")
        try:
            if not self.syntax_ok(selected, interpreter):
                return 2
            return self.interactive([interpreter, str(selected)], log, {"YJL_TUI_TCP_CHOICE": choice})
        finally:
            selected.unlink(missing_ok=True)

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
    def docker_binary() -> list[str] | None:
        if shutil.which("docker"):
            return ["docker"]
        print("未找到 docker 命令，请先在“一键在线安装”中安装 Docker。")
        return None

    @staticmethod
    def compose_binary() -> list[str] | None:
        if shutil.which("docker"):
            result = subprocess.run(["docker", "compose", "version"], text=True, capture_output=True)
            if result.returncode == 0:
                return ["docker", "compose"]
        if shutil.which("docker-compose"):
            return ["docker-compose"]
        print("未找到 Docker Compose 插件或 docker-compose。")
        return None

    def docker_run(
        self,
        args: list[str],
        cwd: Path | None = None,
        timeout: int = 300,
    ) -> int:
        binary = self.docker_binary()
        if not binary:
            return 127
        try:
            result = subprocess.run(
                binary + args,
                text=True,
                capture_output=True,
                cwd=str(cwd) if cwd else None,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            print("Docker 命令超时。")
            return 124
        if result.stdout:
            print(result.stdout, end="" if result.stdout.endswith("\n") else "\n")
        if result.stderr:
            print(result.stderr, end="" if result.stderr.endswith("\n") else "\n")
        return result.returncode

    @staticmethod
    def compose_run(command: list[str], cwd: Path, timeout: int = 300) -> int:
        try:
            result = subprocess.run(
                command,
                text=True,
                capture_output=True,
                cwd=str(cwd),
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            print("Compose 命令超时。")
            return 124
        if result.stdout:
            print(result.stdout, end="" if result.stdout.endswith("\n") else "\n")
        if result.stderr:
            print(result.stderr, end="" if result.stderr.endswith("\n") else "\n")
        return result.returncode

    @staticmethod
    def docker_name(value: str, image: bool = False) -> bool:
        pattern = r"[A-Za-z0-9][A-Za-z0-9._/@:+-]*" if image else r"[A-Za-z0-9][A-Za-z0-9_.-]*"
        return bool(re.fullmatch(pattern, value))

    def docker_status(self, log: Path) -> int:
        if not self.docker_binary():
            return 127
        print("== Docker 版本 ==")
        rc = self.docker_run(["version"], timeout=30)
        print("\n== Docker 信息 ==")
        info_rc = self.docker_run(["info"], timeout=30)
        compose = self.compose_binary()
        if compose:
            print("\n== Compose 版本 ==")
            compose_result = subprocess.run(compose + ["version"], text=True, capture_output=True)
            print(compose_result.stdout or compose_result.stderr, end="")
        if shutil.which("systemctl"):
            print("\n== Docker systemd 状态 ==")
            subprocess.run(["systemctl", "is-active", "docker"], check=False)
        return rc or info_rc

    def docker_containers(self, log: Path) -> int:
        print("== 全部容器 ==")
        return self.docker_run([
            "ps", "-a", "--format",
            "table {{.ID}}\\t{{.Names}}\\t{{.Image}}\\t{{.Status}}\\t{{.Ports}}",
        ])

    def docker_images(self, log: Path) -> int:
        print("== 本地镜像 ==")
        return self.docker_run([
            "image", "ls", "--format",
            "table {{.Repository}}\\t{{.Tag}}\\t{{.ID}}\\t{{.CreatedSince}}\\t{{.Size}}",
        ])

    def docker_logs(self, log: Path) -> int:
        name = input("容器名：").strip()
        tail = input("显示最近多少行 [200]：").strip() or "200"
        if not self.docker_name(name) or not tail.isdigit() or int(tail) <= 0:
            print("容器名或日志行数格式不合法。")
            return 2
        command = ["logs", "--tail", tail, "--timestamps"]
        if input("是否持续跟踪日志？[y/N] ").strip().lower() in {"y", "yes"}:
            command.append("--follow")
            command.append(name)
            binary = self.docker_binary()
            if not binary:
                return 127
            return self.interactive(binary + command, log, pause=False)
        command.append(name)
        return self.docker_run(command)

    def docker_container_action(self, action_id: str, log: Path) -> int:
        name = input("容器名：").strip()
        if not self.docker_name(name):
            print("容器名格式不合法。")
            return 2
        command = {"docker_start": "start", "docker_stop": "stop", "docker_restart": "restart"}[action_id]
        return self.docker_run([command, name])

    def docker_exec(self, log: Path) -> int:
        name = input("容器名：").strip()
        shell = input("容器内 Shell [/bin/sh]：").strip() or "/bin/sh"
        if not self.docker_name(name) or not re.fullmatch(r"/[A-Za-z0-9._/-]+", shell):
            print("容器名或 Shell 路径格式不合法。")
            return 2
        binary = self.docker_binary()
        if not binary:
            return 127
        return self.interactive(binary + ["exec", "-it", name, shell], log, pause=False)

    def docker_pull(self, log: Path) -> int:
        image = input("镜像名（例如 nginx:latest）：").strip()
        if not self.docker_name(image, image=True):
            print("镜像名格式不合法。")
            return 2
        return self.docker_run(["pull", image], timeout=1800)

    def docker_remove(self, log: Path) -> int:
        kind = input("1. 删除容器  2. 删除镜像\n选择 [1]: ").strip() or "1"
        if kind not in {"1", "2"}:
            return 2
        name = input("名称：").strip()
        if not self.docker_name(name, image=kind == "2"):
            print("名称格式不合法。")
            return 2
        if kind == "1":
            force = input("容器正在运行时是否强制删除？[y/N] ").strip().lower() in {"y", "yes"}
            args = ["rm"] + (["--force"] if force else []) + [name]
        else:
            args = ["rmi", name]
        return self.docker_run(args)

    def docker_compose(self, log: Path) -> int:
        compose = self.compose_binary()
        if not compose:
            return 127
        raw_path = input("Compose 项目目录 [当前目录]：").strip() or "."
        project = Path(raw_path).expanduser()
        try:
            project = project.resolve()
        except OSError as exc:
            print(f"项目路径无效：{exc}")
            return 2
        if not project.is_dir():
            print(f"目录不存在：{project}")
            return 2
        files = [name for name in ("compose.yaml", "compose.yml", "docker-compose.yaml", "docker-compose.yml") if (project / name).is_file()]
        if not files:
            print("目录中没有 compose.yaml、compose.yml 或 docker-compose.yml。")
            return 2
        print(f"已发现：{', '.join(files)}")
        print("1. up -d\n2. down\n3. restart\n4. ps\n5. logs\n6. pull\n7. config 校验")
        choice = input("选择 [1]: ").strip() or "1"
        commands = {
            "1": ["up", "-d"],
            "2": ["down"],
            "3": ["restart"],
            "4": ["ps"],
            "5": ["logs", "--tail", "200"],
            "6": ["pull"],
            "7": ["config"],
        }
        if choice not in commands:
            return 2
        command = compose + commands[choice]
        if choice in {"5"}:
            return self.interactive(command, log, cwd=project, pause=False)
        return self.compose_run(command, cwd=project, timeout=1800 if choice in {"1", "6"} else 300)

    def docker_prune(self, log: Path) -> int:
        print("将清理停止容器、未使用网络、悬空镜像和构建缓存。")
        args = ["system", "prune", "--all"]
        if input("是否同时删除未使用的数据卷？[y/N] ").strip().lower() in {"y", "yes"}:
            args.append("--volumes")
        args.append("--force")
        return self.docker_run(args, timeout=1800)

    def docker_daemon_restart(self, log: Path) -> int:
        if shutil.which("systemctl"):
            result = subprocess.run(["systemctl", "restart", "docker"], text=True, capture_output=True)
        elif shutil.which("service"):
            result = subprocess.run(["service", "docker", "restart"], text=True, capture_output=True)
        else:
            print("当前系统没有 systemctl 或 service，无法重启 Docker daemon。")
            return 1
        print(result.stdout or result.stderr, end="")
        return result.returncode

    def lazydocker(self, log: Path) -> int:
        if not shutil.which("lazydocker"):
            print("未找到 lazydocker，请先执行本分类的“安装 lazydocker（官方二进制）”。")
            return 1
        return self.interactive(["lazydocker"], log, pause=False)

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
        if action.get("kind") == "exit":
            self.should_exit = True
            rc = 0
        elif action.get("kind") == "online":
            rc = self.online(action, log)
        elif action.get("kind") == "local_script":
            rc = self.local_script(action, log)
            if action["id"] == "tcp_brutal_install" and rc == 0:
                print("\n安装器完成，开始修复已有 sing-box 配置和结构化订阅……")
                repair_rc = self.tcp_brutal_repair(log)
                rc = repair_rc if repair_rc else rc
        elif action.get("kind") == "tcp_online":
            rc = self.tcp_online(action, log)
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
        elif action["id"] == "system_kernel_maintenance":
            rc = self.system_kernel_maintenance(log)
            input("\n按 Enter 返回菜单...")
        elif action["id"] == "ssl_manage":
            rc = self.ssl_manage(log)
            input("\n按 Enter 返回菜单...")
        elif action["id"] == "nginx_manager":
            rc = run_nginx_manager(STATE)
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
        elif action["id"] == "tcp_brutal_repair":
            rc = self.tcp_brutal_repair(log)
            input("\n按 Enter 返回菜单...")
        elif action.get("tcp_profile"):
            rc = self.tcp_apply_profile(action["tcp_profile"], log)
            input("\n按 Enter 返回菜单...")
        elif action["id"] == "tcp_remove_all":
            rc = self.tcp_remove_all(log)
            input("\n按 Enter 返回菜单...")
        elif action["id"] in {
            "docker_status", "docker_containers", "docker_images", "docker_logs",
            "docker_start", "docker_stop", "docker_restart", "docker_exec", "docker_pull",
            "docker_remove", "docker_compose", "docker_prune", "docker_daemon_restart", "lazydocker",
        }:
            handlers = {
                "docker_status": self.docker_status,
                "docker_containers": self.docker_containers,
                "docker_images": self.docker_images,
                "docker_logs": self.docker_logs,
                "docker_start": lambda current_log: self.docker_container_action("docker_start", current_log),
                "docker_stop": lambda current_log: self.docker_container_action("docker_stop", current_log),
                "docker_restart": lambda current_log: self.docker_container_action("docker_restart", current_log),
                "docker_exec": self.docker_exec,
                "docker_pull": self.docker_pull,
                "docker_remove": self.docker_remove,
                "docker_compose": self.docker_compose,
                "docker_prune": self.docker_prune,
                "docker_daemon_restart": self.docker_daemon_restart,
                "lazydocker": self.lazydocker,
            }
            rc = handlers[action["id"]](log)
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
        visible_capacity = max(1, height - 12)
        start = max(0, min(self.selected, max(0, len(actions) - visible_capacity)))
        screen.addnstr(3, left + 3, "动作（Enter 执行）", width - left - 5, curses.A_BOLD)
        for row, action in enumerate(actions[start:start + visible_capacity]):
            index = start + row
            attr = curses.color_pair(1) | curses.A_BOLD if index == self.selected else 0
            screen.addnstr(5 + row, left + 3, ("❯ " if index == self.selected else "  ") + action["title"], width - left - 5, attr)
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
                if self.should_exit:
                    return


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
            print(f"{action['id']:18} {action['title']}")
        return 0
    if "--check" in sys.argv:
        print(f"配置 OK: {CONFIG}")
        print(f"TCP 方案: {TCP_PROFILES if TCP_PROFILES.is_file() else '未找到'}")
        return 0
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        print("需要在真实终端运行；非交互检查请使用 --check。", file=sys.stderr)
        return 2
    terminal_notice()
    curses.wrapper(manager.run_ui)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
