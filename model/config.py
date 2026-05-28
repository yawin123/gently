from __future__ import annotations

import os
import sys
import tomllib
from dataclasses import dataclass, field
from typing import Any

# Make vendor/ importable even when this module is imported directly.
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "vendor"))

import tomli_w  # noqa: E402


class ConfigError(Exception):
    pass


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _to_dict(obj: Any) -> Any:
    """Recursively convert to a plain dict suitable for tomli_w, omitting Nones."""
    if obj is None:
        return None
    if isinstance(obj, (bool, int, float, str)):
        return obj
    if isinstance(obj, list):
        return [_to_dict(item) for item in obj]
    if isinstance(obj, dict):
        result = {}
        for k, v in obj.items():
            converted = _to_dict(v)
            if converted is not None:
                result[k] = converted
        return result if result else None
    if hasattr(obj, "__dataclass_fields__"):
        result = {}
        for key in obj.__dataclass_fields__:
            val = getattr(obj, key)
            converted = _to_dict(val)
            if converted is not None:
                result[key] = converted
        return result if result else None
    return obj


def _expect_type(value: Any, expected: type, path: str) -> Any:
    if value is None:
        return None
    if not isinstance(value, expected):
        raise ConfigError(
            f"Field '{path}': expected {expected.__name__}, "
            f"got {type(value).__name__}"
        )
    return value


def _extract_list_field(val: Any) -> list[str] | None:
    """Handle both a direct list and a {key: list} subtable (legacy format)."""
    if isinstance(val, list):
        return val
    if isinstance(val, dict):
        for v in val.values():
            if isinstance(v, list):
                return v
    return None


# ---------------------------------------------------------------------------
# to_dict() mixin
# ---------------------------------------------------------------------------

class _ToDictMixin:
    def to_dict(self) -> dict:
        return _to_dict(self) or {}


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class SystemConfig(_ToDictMixin):
    hostname:  str | None       = field(default=None)
    timezone:  str | None       = field(default=None)
    locale:    str | None       = field(default=None)
    locales:   list[str] | None = field(default=None)
    keymap:    str | None       = field(default=None)
    lang:      str | None       = field(default=None)


@dataclass
class Stage3Config(_ToDictMixin):
    arch:               str | None = field(default=None)
    variant:            str | None = field(default=None)
    mirror:             str | None = field(default=None)
    local_path:         str | None = field(default=None)
    tarball_url:        str | None = field(default=None)
    signature_url:      str | None = field(default=None)
    signature_path:     str | None = field(default=None)
    verify_signature:   bool       = True


@dataclass
class PartitionConfig(_ToDictMixin):
    label:         str | None       = field(default=None)
    size:          str | None       = field(default=None)
    filesystem:    str | None       = field(default=None)
    mount:         str | None       = field(default=None)
    mount_options: str | None       = field(default=None)
    flags:         list[str] | None = field(default=None)
    luks:          bool | None      = field(default=None)
    luks_label:    str | None       = field(default=None)


@dataclass
class DiskConfig(_ToDictMixin):
    id:              str | None            = field(default=None)
    device:          str | None            = field(default=None)
    partition_table: str | None            = field(default=None)
    boot_mode:       str | None            = field(default=None)
    confirm_wipe:    bool                  = True
    partitions:      list[PartitionConfig] = field(default_factory=list)


@dataclass
class PortagePackagesConfig(_ToDictMixin):
    use:              dict[str, list[str]] | None = field(default=None)
    accept_keywords:  dict[str, list[str]] | None = field(default=None)
    license:          dict[str, list[str]] | None = field(default=None)
    mask:             list[str] | None            = field(default=None)
    unmask:           list[str] | None            = field(default=None)
    env:              dict[str, str] | None       = field(default=None)


@dataclass
class PortageRepoConfig(_ToDictMixin):
    name:      str | None = field(default=None)
    sync_type: str | None = field(default=None)
    sync_uri:  str | None = field(default=None)


@dataclass
class PortageProfileConfig(_ToDictMixin):
    name: str | None = field(default=None)


@dataclass
class PortageConfig(_ToDictMixin):
    cflags:          str | None             = field(default=None)
    cxxflags:        str | None             = field(default=None)
    makeopts:        str | None             = field(default=None)
    cpu_flags:       list[str] | None       = field(default=None)
    use:             list[str] | None       = field(default=None)
    accept_keywords: str | None             = field(default=None)
    accept_license:  str | None             = field(default=None)
    video_cards:     list[str] | None       = field(default=None)
    input_devices:   list[str] | None       = field(default=None)
    mirrors:         list[str] | None       = field(default=None)
    features:        list[str] | None       = field(default=None)
    getbinpkg:       bool | None            = field(default=None)
    binpkg_format:   str | None             = field(default=None)
    binhost:         str | None             = field(default=None)
    profile:         PortageProfileConfig | None  = field(default=None)
    repos:           list[PortageRepoConfig] | None = field(default=None)
    packages:        PortagePackagesConfig | None   = field(default=None)


@dataclass
class KernelCustomConfig(_ToDictMixin):
    config_path: str | None = field(default=None)


@dataclass
class KernelConfig(_ToDictMixin):
    method:        str | None              = field(default=None)
    extra_modules: list[str] | None        = field(default=None)
    custom:        KernelCustomConfig | None = field(default=None)


@dataclass
class BootloaderGrubConfig(_ToDictMixin):
    platforms:    list[str] | None = field(default=None)
    install_disk: str | None       = field(default=None)
    timeout:      int | None       = field(default=None)
    cmdline_extra: list[str] | None = field(default=None)


@dataclass
class BootloaderConfig(_ToDictMixin):
    type: str | None                  = field(default=None)
    grub: BootloaderGrubConfig | None = field(default=None)


@dataclass
class NetworkInterfaceConfig(_ToDictMixin):
    mode:    str | None       = field(default=None)
    address: str | None       = field(default=None)
    gateway: str | None       = field(default=None)
    dns:     list[str] | None = field(default=None)


@dataclass
class NetworkConfig(_ToDictMixin):
    interfaces: dict[str, NetworkInterfaceConfig] | None = field(default=None)


@dataclass
class ServicesRolesConfig(_ToDictMixin):
    cron:      str | None = field(default=None)
    logging:   str | None = field(default=None)
    network:   str | None = field(default=None)
    ssh:       str | None = field(default=None)
    ntp:       str | None = field(default=None)
    firewall:  str | None = field(default=None)
    printing:  str | None = field(default=None)
    bluetooth: str | None = field(default=None)


@dataclass
class ServicesExtraConfig(_ToDictMixin):
    enable: list[str] = field(default_factory=list)


@dataclass
class ServicesConfig(_ToDictMixin):
    roles:   ServicesRolesConfig | None  = field(default=None)
    network: NetworkConfig | None        = field(default=None)
    extra:   ServicesExtraConfig | None  = field(default=None)


@dataclass
class UserAccountConfig(_ToDictMixin):
    name:                str | None       = field(default=None)
    password:            str | None       = field(default=None)
    password_hash:       str | None       = field(default=None)
    groups:              list[str] | None = field(default=None)
    shell:               str | None       = field(default=None)
    comment:             str | None       = field(default=None)
    create_home:         bool | None      = field(default=None)
    ssh_authorized_keys: list[str] | None = field(default=None)


@dataclass
class UsersConfig(_ToDictMixin):
    credentials_file:        str | None            = field(default=None)
    credentials_file_shadow: str | None            = field(default=None)
    accounts:                list[UserAccountConfig] = field(default_factory=list)


@dataclass
class PackagesConfig(_ToDictMixin):
    extra:       list[str]       = field(default_factory=list)
    source_only: list[str] | None = field(default=None)


@dataclass
class DistccConfig(_ToDictMixin):
    enabled:           bool            = False
    hosts:             list[str] | None = field(default=None)
    makeopts_jobs:     int | None       = field(default=None)
    pump_mode:         bool             = False
    install_on_target: bool             = False
    port:              int | None       = field(default=None)
    distcc_dir:        str | None       = field(default=None)


@dataclass
class GentlyConfig(_ToDictMixin):
    system:     SystemConfig | None     = field(default=None)
    stage3:     Stage3Config | None     = field(default=None)
    disks:      list[DiskConfig]        = field(default_factory=list)
    portage:    PortageConfig | None    = field(default=None)
    kernel:     KernelConfig | None     = field(default=None)
    bootloader: BootloaderConfig | None = field(default=None)
    services:   ServicesConfig | None   = field(default=None)
    users:      UsersConfig | None      = field(default=None)
    packages:   PackagesConfig | None   = field(default=None)
    distcc:     DistccConfig | None     = field(default=None)


# ---------------------------------------------------------------------------
# Section loaders
# ---------------------------------------------------------------------------

def _load_system(raw: dict) -> SystemConfig | None:
    s = raw.get("system")
    if not isinstance(s, dict):
        return None
    return SystemConfig(
        hostname=_expect_type(s.get("hostname"), str, "system.hostname"),
        timezone=_expect_type(s.get("timezone"), str, "system.timezone"),
        locale=_expect_type(s.get("locale"), str, "system.locale"),
        locales=_expect_type(s.get("locales"), list, "system.locales"),
        keymap=_expect_type(s.get("keymap"), str, "system.keymap"),
        lang=_expect_type(s.get("lang"), str, "system.lang"),
    )


def _load_stage3(raw: dict) -> Stage3Config | None:
    s = raw.get("stage3")
    if not isinstance(s, dict):
        return None
    return Stage3Config(
        arch=_expect_type(s.get("arch"), str, "stage3.arch"),
        variant=_expect_type(s.get("variant"), str, "stage3.variant"),
        mirror=_expect_type(s.get("mirror"), str, "stage3.mirror"),
        local_path=_expect_type(s.get("local_path"), str, "stage3.local_path"),
        tarball_url=_expect_type(s.get("tarball_url"), str, "stage3.tarball_url"),
        signature_url=_expect_type(s.get("signature_url"), str, "stage3.signature_url"),
        signature_path=_expect_type(s.get("signature_path"), str, "stage3.signature_path"),
        verify_signature=s.get("verify_signature", True),
    )


def _load_partition(p: dict, path: str) -> PartitionConfig:
    return PartitionConfig(
        label=_expect_type(p.get("label"), str, f"{path}.label"),
        size=_expect_type(p.get("size"), str, f"{path}.size"),
        filesystem=_expect_type(p.get("filesystem"), str, f"{path}.filesystem"),
        mount=_expect_type(p.get("mount"), str, f"{path}.mount"),
        mount_options=_expect_type(p.get("mount_options"), str, f"{path}.mount_options"),
        flags=_expect_type(p.get("flags"), list, f"{path}.flags"),
        luks=_expect_type(p.get("luks"), bool, f"{path}.luks"),
        luks_label=_expect_type(p.get("luks_label"), str, f"{path}.luks_label"),
    )


def _load_disks(raw: dict) -> list[DiskConfig]:
    disks_raw = raw.get("disks", [])
    if not isinstance(disks_raw, list):
        return []
    result = []
    for i, d in enumerate(disks_raw):
        if not isinstance(d, dict):
            continue
        partitions = [
            _load_partition(p, f"disks[{i}].partitions[{j}]")
            for j, p in enumerate(d.get("partitions", []))
            if isinstance(p, dict)
        ]
        result.append(DiskConfig(
            id=_expect_type(d.get("id"), str, f"disks[{i}].id"),
            device=_expect_type(d.get("device"), str, f"disks[{i}].device"),
            partition_table=_expect_type(d.get("partition_table"), str, f"disks[{i}].partition_table"),
            boot_mode=_expect_type(d.get("boot_mode"), str, f"disks[{i}].boot_mode"),
            confirm_wipe=d.get("confirm_wipe", True),
            partitions=partitions,
        ))
    return result


def _load_portage_packages(p: dict) -> PortagePackagesConfig | None:
    if not isinstance(p, dict):
        return None
    return PortagePackagesConfig(
        use=_expect_type(p.get("use"), dict, "portage.packages.use"),
        accept_keywords=_expect_type(p.get("accept_keywords"), dict, "portage.packages.accept_keywords"),
        license=_expect_type(p.get("license"), dict, "portage.packages.license"),
        mask=_extract_list_field(p.get("mask")),
        unmask=_extract_list_field(p.get("unmask")),
        env=_expect_type(p.get("env"), dict, "portage.packages.env"),
    )


def _load_portage(raw: dict) -> PortageConfig | None:
    p = raw.get("portage")
    if not isinstance(p, dict):
        return None

    profile_raw = p.get("profile")
    profile = None
    if isinstance(profile_raw, dict):
        profile = PortageProfileConfig(
            name=_expect_type(profile_raw.get("name"), str, "portage.profile.name")
        )

    repos_raw = p.get("repos", [])
    repos = None
    if isinstance(repos_raw, list) and repos_raw:
        repos = [
            PortageRepoConfig(
                name=r.get("name"),
                sync_type=r.get("sync_type"),
                sync_uri=r.get("sync_uri"),
            )
            for r in repos_raw
            if isinstance(r, dict)
        ]

    packages_raw = p.get("packages")
    packages = _load_portage_packages(packages_raw) if isinstance(packages_raw, dict) else None

    return PortageConfig(
        cflags=_expect_type(p.get("cflags"), str, "portage.cflags"),
        cxxflags=_expect_type(p.get("cxxflags"), str, "portage.cxxflags"),
        makeopts=_expect_type(p.get("makeopts"), str, "portage.makeopts"),
        cpu_flags=_expect_type(p.get("cpu_flags"), list, "portage.cpu_flags"),
        use=_expect_type(p.get("use"), list, "portage.use"),
        accept_keywords=_expect_type(p.get("accept_keywords"), str, "portage.accept_keywords"),
        accept_license=_expect_type(p.get("accept_license"), str, "portage.accept_license"),
        video_cards=_expect_type(p.get("video_cards"), list, "portage.video_cards"),
        input_devices=_expect_type(p.get("input_devices"), list, "portage.input_devices"),
        mirrors=_expect_type(p.get("mirrors"), list, "portage.mirrors"),
        features=_expect_type(p.get("features"), list, "portage.features"),
        getbinpkg=p.get("getbinpkg"),
        binpkg_format=_expect_type(p.get("binpkg_format"), str, "portage.binpkg_format"),
        binhost=_expect_type(p.get("binhost"), str, "portage.binhost"),
        profile=profile,
        repos=repos,
        packages=packages,
    )


def _load_kernel(raw: dict) -> KernelConfig | None:
    k = raw.get("kernel")
    if not isinstance(k, dict):
        return None
    custom_raw = k.get("custom")
    custom = None
    if isinstance(custom_raw, dict):
        custom = KernelCustomConfig(
            config_path=_expect_type(custom_raw.get("config_path"), str, "kernel.custom.config_path")
        )
    return KernelConfig(
        method=_expect_type(k.get("method"), str, "kernel.method"),
        extra_modules=_expect_type(k.get("extra_modules"), list, "kernel.extra_modules"),
        custom=custom,
    )


def _load_bootloader(raw: dict) -> BootloaderConfig | None:
    b = raw.get("bootloader")
    if not isinstance(b, dict):
        return None
    grub_raw = b.get("grub")
    grub = None
    if isinstance(grub_raw, dict):
        grub = BootloaderGrubConfig(
            platforms=_expect_type(grub_raw.get("platforms"), list, "bootloader.grub.platforms"),
            install_disk=_expect_type(grub_raw.get("install_disk"), str, "bootloader.grub.install_disk"),
            timeout=_expect_type(grub_raw.get("timeout"), int, "bootloader.grub.timeout"),
            cmdline_extra=_expect_type(grub_raw.get("cmdline_extra"), list, "bootloader.grub.cmdline_extra"),
        )
    return BootloaderConfig(
        type=_expect_type(b.get("type"), str, "bootloader.type"),
        grub=grub,
    )


def _load_services(raw: dict) -> ServicesConfig | None:
    s = raw.get("services")
    if not isinstance(s, dict):
        return None

    roles_raw = s.get("roles")
    roles = None
    if isinstance(roles_raw, dict):
        roles = ServicesRolesConfig(
            cron=roles_raw.get("cron"),
            logging=roles_raw.get("logging"),
            network=roles_raw.get("network"),
            ssh=roles_raw.get("ssh"),
            ntp=roles_raw.get("ntp"),
            firewall=roles_raw.get("firewall"),
            printing=roles_raw.get("printing"),
            bluetooth=roles_raw.get("bluetooth"),
        )

    network_raw = s.get("network")
    network = None
    if isinstance(network_raw, dict):
        interfaces_raw = network_raw.get("interfaces")
        interfaces = None
        if isinstance(interfaces_raw, dict):
            interfaces = {
                name: NetworkInterfaceConfig(
                    mode=iface.get("mode"),
                    address=iface.get("address"),
                    gateway=iface.get("gateway"),
                    dns=iface.get("dns"),
                )
                for name, iface in interfaces_raw.items()
                if isinstance(iface, dict)
            }
        network = NetworkConfig(interfaces=interfaces or None)

    extra_raw = s.get("extra")
    extra = None
    if isinstance(extra_raw, dict):
        extra = ServicesExtraConfig(enable=extra_raw.get("enable", []))

    return ServicesConfig(roles=roles, network=network, extra=extra)


def _load_users(raw: dict) -> UsersConfig | None:
    u = raw.get("users")
    if not isinstance(u, dict):
        return None

    accounts_raw = u.get("accounts", [])
    # Handle both [[users.accounts]] (list) and [users.accounts] (single table)
    if isinstance(accounts_raw, dict):
        accounts_raw = [accounts_raw]

    accounts = [
        UserAccountConfig(
            name=a.get("name"),
            password=a.get("password"),
            password_hash=a.get("password_hash"),
            groups=a.get("groups"),
            shell=a.get("shell"),
            comment=a.get("comment"),
            create_home=a.get("create_home"),
            ssh_authorized_keys=a.get("ssh_authorized_keys"),
        )
        for a in (accounts_raw if isinstance(accounts_raw, list) else [])
        if isinstance(a, dict)
    ]

    return UsersConfig(
        credentials_file=_expect_type(u.get("credentials_file"), str, "users.credentials_file"),
        credentials_file_shadow=_expect_type(u.get("credentials_file_shadow"), str, "users.credentials_file_shadow"),
        accounts=accounts,
    )


def _load_packages(raw: dict) -> PackagesConfig | None:
    p = raw.get("packages")
    if not isinstance(p, dict):
        return None
    return PackagesConfig(
        extra=p.get("extra", []),
        source_only=_expect_type(p.get("source_only"), list, "packages.source_only"),
    )


def _load_distcc(raw: dict) -> DistccConfig | None:
    d = raw.get("distcc")
    if not isinstance(d, dict):
        return None
    return DistccConfig(
        enabled=d.get("enabled", False),
        hosts=_expect_type(d.get("hosts"), list, "distcc.hosts"),
        makeopts_jobs=_expect_type(d.get("makeopts_jobs"), int, "distcc.makeopts_jobs"),
        pump_mode=d.get("pump_mode", False),
        install_on_target=d.get("install_on_target", False),
        port=_expect_type(d.get("port"), int, "distcc.port"),
        distcc_dir=_expect_type(d.get("distcc_dir"), str, "distcc.distcc_dir"),
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def load_config(path: str) -> GentlyConfig:
    """
    Load a partial or complete config.toml.
    Absent fields remain as None in the model.
    Raises ConfigError if the TOML is malformed or contains incorrect types.
    Returns an empty GentlyConfig if the file does not exist.
    """
    if not os.path.exists(path):
        return GentlyConfig()

    try:
        with open(path, "rb") as f:
            raw = tomllib.load(f)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"Malformed TOML in '{path}': {exc}") from exc

    return GentlyConfig(
        system=_load_system(raw),
        stage3=_load_stage3(raw),
        disks=_load_disks(raw),
        portage=_load_portage(raw),
        kernel=_load_kernel(raw),
        bootloader=_load_bootloader(raw),
        services=_load_services(raw),
        users=_load_users(raw),
        packages=_load_packages(raw),
        distcc=_load_distcc(raw),
    )


def save_config(config: GentlyConfig, path: str) -> None:
    """
    Serialize GentlyConfig to TOML and write to path. None fields are omitted.
    """
    data = config.to_dict()
    with open(path, "wb") as f:
        tomli_w.dump(data, f)
