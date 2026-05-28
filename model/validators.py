from __future__ import annotations

import re

from model.config import GentlyConfig
from util.disk import disk_size_bytes, parse_size_to_bytes


def _parse_size_percent(size_expr: str | None) -> float | None:
    if not size_expr:
        return None
    s = size_expr.strip()
    m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*%", s)
    if not m:
        return None
    return float(m.group(1))


def validate_coherence(config: GentlyConfig) -> list[str]:
    """
    Validate cross-section coherence. Called just before installation starts,
    not during config loading.
    Returns a list of error messages. An empty list means the config is coherent.
    """
    errors: list[str] = []

    # v1: only a single disk is supported
    if len(config.disks) > 1:
        errors.append(
            "Multiple disks are not supported in v1. "
            "Only the first [[disks]] entry would be processed. "
            "Remove extra [[disks]] entries to continue."
        )

    # UEFI boot requires exactly one ESP partition on that disk
    for disk in config.disks:
        if disk.boot_mode == "uefi":
            esp_count = sum(
                1 for p in disk.partitions
                if p.flags and "esp" in p.flags
            )
            if esp_count != 1:
                label = disk.device or disk.id or "unknown"
                errors.append(
                    f"Disk '{label}' uses boot_mode='uefi' but has {esp_count} "
                    f"ESP partition(s) (exactly 1 required, with flags=[\"esp\"])."
                )

    # Explicit partition sizes must not exceed disk capacity when capacity is known.
    for disk in config.disks:
        percent_total = 0.0
        allocated = 0
        for part in disk.partitions:
            pct = _parse_size_percent(part.size)
            if pct is not None:
                percent_total += pct
                continue

            parsed = parse_size_to_bytes(part.size)
            if parsed is None:
                continue
            allocated += parsed

        if percent_total > 100.0:
            label = disk.device or disk.id or "unknown"
            errors.append(
                f"Disk '{label}' has percentage partition sizes totaling {percent_total:.2f}%, "
                "which exceeds 100%."
            )

        disk_size = disk_size_bytes(disk.device)
        if disk_size is None:
            continue

        if allocated > disk_size:
            label = disk.device or disk.id or "unknown"
            errors.append(
                f"Disk '{label}' has explicit partition sizes totaling {allocated} bytes, "
                f"which exceeds detected disk size {disk_size} bytes."
            )

        # Percentage values are interpreted against total disk size.
        required = allocated + int(disk_size * (percent_total / 100.0))
        if required > disk_size:
            label = disk.device or disk.id or "unknown"
            errors.append(
                f"Disk '{label}' allocates {allocated} bytes plus {percent_total:.2f}% of disk size, "
                f"which exceeds detected disk size {disk_size} bytes."
            )

    # distcc: hosts must not be empty when enabled
    if config.distcc and config.distcc.enabled:
        if not config.distcc.hosts:
            errors.append(
                "distcc.enabled is true but distcc.hosts is empty. "
                "Add at least one distccd host."
            )

    # kernel: menuconfig/custom require a config_path
    if config.kernel and config.kernel.method in ("menuconfig", "custom"):
        has_path = (
            config.kernel.custom is not None
            and config.kernel.custom.config_path is not None
        )
        if not has_path:
            errors.append(
                f"kernel.method='{config.kernel.method}' requires "
                f"[kernel.custom] with config_path set."
            )

    # bootloader: grub type requires [bootloader.grub] section
    if config.bootloader and config.bootloader.type == "grub":
        if config.bootloader.grub is None:
            errors.append(
                "bootloader.type='grub' requires a [bootloader.grub] section."
            )

    # systemd variant: logging and ntp roles must be 'none' or absent
    if config.stage3 and config.stage3.variant and "systemd" in config.stage3.variant:
        if config.services and config.services.roles:
            roles = config.services.roles
            if roles.logging and roles.logging != "none":
                errors.append(
                    f"stage3.variant contains 'systemd' but services.roles.logging "
                    f"is '{roles.logging}'. systemd uses journald implicitly; "
                    f"set logging to 'none' or omit it."
                )
            if roles.ntp and roles.ntp != "none":
                errors.append(
                    f"stage3.variant contains 'systemd' but services.roles.ntp "
                    f"is '{roles.ntp}'. systemd uses systemd-timesyncd implicitly; "
                    f"set ntp to 'none' or omit it."
                )

    return errors
