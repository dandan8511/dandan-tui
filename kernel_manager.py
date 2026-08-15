"""Pure planning and parsing helpers for system-kernel maintenance."""
from __future__ import annotations

from dataclasses import dataclass
import re


SUPPORTED_RELEASES = {
    ("debian", "11"),
    ("debian", "12"),
    ("debian", "13"),
    ("ubuntu", "22.04"),
    ("ubuntu", "24.04"),
    ("ubuntu", "26.04"),
}
SUPPORTED_ARCHITECTURES = {"amd64", "arm64"}


@dataclass(frozen=True)
class KernelIdentity:
    distro_id: str
    version_id: str
    codename: str
    architecture: str


@dataclass(frozen=True)
class PackagePlan:
    label: str
    packages: tuple[str, ...]
    source: str


def supported_distribution(identity: KernelIdentity) -> bool:
    return (
        identity.architecture in SUPPORTED_ARCHITECTURES
        and (identity.distro_id, identity.version_id) in SUPPORTED_RELEASES
    )


def recommended_apt_plan(
    identity: KernelIdentity,
    candidates: dict[str, str],
    preferred_track: str = "stable",
) -> PackagePlan | None:
    """Return an install plan only when every package has an APT candidate."""
    if not supported_distribution(identity):
        return None
    packages: tuple[str, ...]
    label: str
    if identity.distro_id == "debian":
        arch = identity.architecture
        packages = (f"linux-image-{arch}", f"linux-headers-{arch}")
        label = f"Debian {identity.version_id} 官方稳定内核"
    elif preferred_track == "hwe":
        version = identity.version_id
        packages = (f"linux-generic-hwe-{version}", f"linux-headers-generic-hwe-{version}")
        label = f"Ubuntu {version} HWE 内核"
    else:
        packages = ("linux-generic", "linux-headers-generic")
        label = f"Ubuntu {identity.version_id} 官方稳定内核"
    if not all(candidates.get(package) for package in packages):
        return None
    return PackagePlan(label, packages, "configured-apt")


def mainline_package_plan(version: str, page: str, architecture: str) -> PackagePlan | None:
    """Return package names only for complete, successful AMD64 Mainline builds."""
    if architecture != "amd64" or "Test amd64 succeeded" not in page:
        return None
    if "CHECKSUMS" not in page or "CHECKSUMS.gpg" not in page:
        return None
    package_names = tuple(
        match.group(1)
        for match in re.finditer(r"""href=["']([^"']+_(?:all|amd64)\.deb)["']""", page)
    )
    if not package_names:
        package_names = tuple(
            match.group(0)
            for match in re.finditer(
                r"linux-(?:headers|image-unsigned|modules)-[A-Za-z0-9.+-]+_[A-Za-z0-9.+-]+_(?:all|amd64)\.deb",
                page,
            )
        )
    required = ("linux-headers-", "linux-image-unsigned-", "linux-modules-")
    if not all(any(name.startswith(prefix) for name in package_names) for prefix in required):
        return None
    if not any(name.endswith("_all.deb") and name.startswith("linux-headers-") for name in package_names):
        return None
    return PackagePlan(f"Ubuntu Mainline {version}", package_names, "kernel.ubuntu.com")


def parse_grub_menu_entries(config_text: str) -> tuple[str, ...]:
    """Parse GRUB menuentry/submenu braces into full, stable entry paths."""
    entries: list[str] = []
    stack: list[str] = []
    pending_submenus: list[str] = []
    token_re = re.compile(r"(?:submenu|menuentry)\s+'([^']+)'|[{}]")
    for match in token_re.finditer(config_text):
        title = match.group(1)
        token = match.group(0)
        if title is not None:
            if token.startswith("submenu"):
                pending_submenus.append(title)
            else:
                entries.append(">".join((*stack, title)))
            continue
        if token == "{" and pending_submenus:
            stack.append(pending_submenus.pop())
        elif token == "}" and stack:
            stack.pop()
    return tuple(entries)
