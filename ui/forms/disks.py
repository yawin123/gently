from __future__ import annotations

import re
import subprocess

from typing import Any

from model.config import DiskConfig, GentlyConfig, PartitionConfig
from ui.abstract import FieldSpec, FormSpec, UIBackend
from ui.forms.base import SectionForm

_PARTITION_TABLES = ["gpt", "msdos"]
_BOOT_MODES       = ["uefi", "bios"]
_FILESYSTEMS      = ["ext4", "ext3", "xfs", "btrfs", "f2fs", "vfat", "swap", "none"]

_FLAGS_GPT = [
    "esp", "boot", "bios_grub", "legacy_boot", "msftdata", "msftres",
    "raid", "lvm", "swap", "hidden", "diag",
]
_FLAGS_MSDOS = [
    "boot", "lba", "raid", "lvm", "swap", "hidden", "diag",
]


def _format_bytes(size: int | None) -> str:
    if size is None or size < 0:
        return "unknown"
    units = ["B", "K", "M", "G", "T", "P"]
    value = float(size)
    idx = 0
    while value >= 1024 and idx < len(units) - 1:
        value /= 1024
        idx += 1
    if idx == 0:
        return f"{int(value)}{units[idx]}"
    return f"{value:.1f}{units[idx]}"


def _parse_partition_size(size_expr: str | None) -> int | None:
    if not size_expr:
        return None
    s = size_expr.strip()
    if not s or s.endswith("%"):
        return None
    m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*([kmgtp]?)(?:i?b)?", s, re.IGNORECASE)
    if not m:
        return None
    num = float(m.group(1))
    unit = m.group(2).upper()
    multiplier = {
        "": 1,
        "K": 1024,
        "M": 1024 ** 2,
        "G": 1024 ** 3,
        "T": 1024 ** 4,
        "P": 1024 ** 5,
    }[unit]
    return int(num * multiplier)


def _device_path_from_choice(choice: str | None) -> str | None:
    if not choice:
        return None
    return choice.split(" ", 1)[0]


def _device_choice(path: str, size_bytes: int | None) -> str:
    return f"{path} ({_format_bytes(size_bytes)})"


def _list_devices() -> list[tuple[str, int | None]]:
    """Return available disk device paths and sizes in bytes."""
    try:
        out = subprocess.run(
            ["lsblk", "-b", "-d", "-n", "-o", "NAME,SIZE,TYPE"],
            capture_output=True, text=True, timeout=5,
        )
        devices: list[tuple[str, int | None]] = []
        for line in out.stdout.strip().splitlines():
            parts = line.split()
            if len(parts) < 3 or parts[2] != "disk":
                continue
            size_bytes: int | None = None
            try:
                size_bytes = int(parts[1])
            except ValueError:
                pass
            devices.append((f"/dev/{parts[0]}", size_bytes))
        return devices if devices else [("/dev/sda", None)]
    except Exception:
        return [("/dev/sda", None)]


def _disk_size_bytes(device_path: str | None) -> int | None:
    if not device_path:
        return None
    try:
        out = subprocess.run(
            ["lsblk", "-b", "-n", "-o", "SIZE", device_path],
            capture_output=True, text=True, timeout=5,
        )
        first = out.stdout.strip().splitlines()
        if not first:
            return None
        return int(first[0].strip())
    except Exception:
        return None


def _available_partition_bytes(
    disk_size_bytes: int | None,
    partitions: list[PartitionConfig],
    editing_index: int | None,
) -> int | None:
    if disk_size_bytes is None:
        return None
    allocated = 0
    for idx, part in enumerate(partitions):
        if editing_index is not None and idx == editing_index:
            continue
        parsed = _parse_partition_size(part.size)
        if parsed is None:
            continue
        allocated += parsed
    return max(0, disk_size_bytes - allocated)


def _dedupe_keep_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _suggest_partition_flags(
    partition_table: str | None,
    boot_mode: str | None,
    part: PartitionConfig,
) -> list[str]:
    base = _FLAGS_GPT if partition_table == "gpt" else _FLAGS_MSDOS
    recommended: list[str] = []

    mount = (part.mount or "").strip().lower()
    fs = (part.filesystem or "").strip().lower()

    if fs == "swap":
        recommended.append("swap")

    if boot_mode == "uefi":
        if fs == "vfat" or mount in ("/boot/efi", "/efi"):
            recommended.extend(["esp", "boot"])
    elif boot_mode == "bios" and partition_table == "gpt":
        recommended.append("bios_grub")

    if mount == "/boot":
        recommended.append("boot")

    return _dedupe_keep_order(recommended + base)


class DisksForm(SectionForm):
    section_name = "Disk layout"
    section_key  = "disks"

    # ------------------------------------------------------------------
    # Completeness
    # ------------------------------------------------------------------

    def is_complete(self, config: GentlyConfig) -> bool:
        if not config.disks:
            return False
        disk = config.disks[0]
        if not (disk.device and disk.partition_table and disk.boot_mode):
            return False
        return any(
            p.label and p.size and p.filesystem
            for p in disk.partitions
        )

    # ------------------------------------------------------------------
    # Sub-form builders
    # ------------------------------------------------------------------

    def _disk_form(self, disk: DiskConfig, partitions: list[PartitionConfig]) -> FormSpec:
        part_summary = f"{len(partitions)} configured"
        devices = _list_devices()
        options = [_device_choice(path, size_bytes) for path, size_bytes in devices]
        current_device = disk.device
        default_device = options[0] if options else None
        if current_device:
            known = next((opt for opt, (path, _sz) in zip(options, devices) if path == current_device), None)
            default_device = known or _device_choice(current_device, _disk_size_bytes(current_device))
            if known is None and default_device not in options:
                options = [default_device] + options

        return FormSpec(
            title="Disk layout — disk settings",
            subtitle="v1: only one disk is supported",
            fields=[
                FieldSpec(
                    key="device",
                    label="Device",
                    type="choice",
                    default=default_device,
                    options=options,
                    help="Target disk for installation. Size is shown in the selector.",
                ),
                FieldSpec(
                    key="partition_table",
                    label="Partition table",
                    type="choice",
                    default=disk.partition_table or "gpt",
                    options=_PARTITION_TABLES,
                    help="gpt is recommended for modern UEFI systems. msdos is legacy/BIOS style.",
                ),
                FieldSpec(
                    key="boot_mode",
                    label="Boot mode",
                    type="choice",
                    default=disk.boot_mode or "uefi",
                    options=_BOOT_MODES,
                    help="Select the firmware boot mode for this install target.",
                ),
                FieldSpec(
                    key="confirm_wipe",
                    label="Confirm before wipe",
                    type="bool",
                    default=disk.confirm_wipe,
                    help="Prompt for confirmation before erasing the disk",
                ),
                FieldSpec(
                    key="partitions_editor",
                    label="Partitions",
                    type="subsection",
                    default=part_summary,
                    required=False,
                    help="Press Enter to open partition list, then add/edit/delete entries.",
                ),
            ],
        )

    def _partition_form(
        self,
        part: PartitionConfig,
        idx: int,
        existing: bool,
        available_bytes: int | None,
        partition_table: str | None,
        boot_mode: str | None,
    ) -> FormSpec:
        actions = [
            ("Save", "save"),
            ("Cancel", "cancel"),
        ]
        if existing:
            actions.insert(1, ("Delete", "delete"))

        size_help = "e.g. 512M, 32G, 100%. Percentages are of total disk size."
        subtitle = None
        if available_bytes is not None:
            size_help += f". Max available now: {_format_bytes(available_bytes)}"
            subtitle = f"Max available now: {_format_bytes(available_bytes)}"
        else:
            subtitle = "Max available now: unknown"

        flags_options = _suggest_partition_flags(partition_table, boot_mode, part)
        flags_help = (
            "Suggested flags are context-aware. "
            "Common examples: UEFI EFI partition -> esp (often boot too), "
            "BIOS on GPT -> bios_grub, swap partition -> swap."
        )

        return FormSpec(
            title=f"Disk layout — partition {idx}",
            subtitle=subtitle,
            fields=[
                FieldSpec(
                    key="label",
                    label="Label",
                    type="text",
                    default=part.label,
                    help="Partition label, e.g. boot, root, swap",
                ),
                FieldSpec(
                    key="size",
                    label="Size",
                    type="text",
                    default=part.size,
                    help=size_help,
                ),
                FieldSpec(
                    key="filesystem",
                    label="Filesystem",
                    type="choice",
                    default=part.filesystem or "ext4",
                    options=_FILESYSTEMS,
                    help="Use swap for swap partitions, vfat for EFI system partition, ext4 as common default.",
                ),
                FieldSpec(
                    key="mount",
                    label="Mount point",
                    type="text",
                    default=part.mount,
                    required=False,
                    help="e.g. /boot/efi, /, /home  (blank for swap)",
                ),
                FieldSpec(
                    key="mount_options",
                    label="Mount options",
                    type="text",
                    default=part.mount_options,
                    required=False,
                    help="Optional comma-separated options, e.g. noatime,nodiratime.",
                ),
                FieldSpec(
                    key="flags",
                    label="Flags",
                    type="list",
                    default=list(part.flags) if part.flags else None,
                    options=flags_options,
                    required=False,
                    help=flags_help,
                ),
            ],
            actions=actions,
        )

    @staticmethod
    def _partition_from_values(values: dict) -> PartitionConfig:
        return PartitionConfig(
            label=values.get("label") or None,
            size=values.get("size") or None,
            filesystem=values.get("filesystem") or None,
            mount=values.get("mount") or None,
            mount_options=values.get("mount_options") or None,
            flags=values.get("flags") or None,
        )

    @staticmethod
    def _partition_section_items(partitions: list[PartitionConfig]) -> list[tuple[str, dict[str, Any]]]:
        items: list[tuple[str, dict[str, Any]]] = []
        for index, part in enumerate(partitions, start=1):
            entry: dict[str, Any] = {}
            if part.label:
                entry["label"] = part.label
            if part.size:
                entry["size"] = part.size
            if part.filesystem:
                entry["filesystem"] = part.filesystem
            if part.mount:
                entry["mount"] = part.mount
            if part.mount_options:
                entry["mount_options"] = part.mount_options
            if part.flags:
                entry["flags"] = " ".join(part.flags)
            if part.luks is not None:
                entry["luks"] = part.luks
            if part.luks_label:
                entry["luks_label"] = part.luks_label
            items.append((f"partition {index}", entry))
        return items

    # ------------------------------------------------------------------
    # Fallback for the base-class default cycle (rarely used directly)
    # ------------------------------------------------------------------

    def build_form(self, config: GentlyConfig) -> FormSpec:
        disk = config.disks[0] if config.disks else DiskConfig()
        return self._disk_form(disk, list(disk.partitions))

    def apply(self, config: GentlyConfig, values: dict) -> GentlyConfig:
        disk = DiskConfig(
            device=values.get("device") or None,
            partition_table=values.get("partition_table") or None,
            boot_mode=values.get("boot_mode") or None,
            confirm_wipe=bool(values.get("confirm_wipe", True)),
        )
        config.disks = [disk]
        return config

    # ------------------------------------------------------------------
    # Overridden run() — orchestrates disk + partition forms
    # ------------------------------------------------------------------

    def run(self, config: GentlyConfig, backend: UIBackend) -> GentlyConfig:  # type: ignore[override]
        existing_disk = config.disks[0] if config.disks else DiskConfig()
        disk = DiskConfig(
            device=existing_disk.device,
            partition_table=existing_disk.partition_table,
            boot_mode=existing_disk.boot_mode,
            confirm_wipe=existing_disk.confirm_wipe,
        )
        partitions = list(existing_disk.partitions)

        while True:
            disk_values = backend.show_form(self._disk_form(disk, partitions))
            if disk_values is None:
                return config

            # Persist edits done in disk settings even when opening subsection.
            base_values = disk_values.get("__values__", disk_values)
            disk.device = _device_path_from_choice(base_values.get("device")) or None
            disk.partition_table = base_values.get("partition_table") or None
            disk.boot_mode = base_values.get("boot_mode") or None
            disk.confirm_wipe = bool(base_values.get("confirm_wipe", True))

            if disk_values.get("__action__") != "subsection":
                break
            if disk_values.get("__field__") != "partitions_editor":
                continue

            while True:
                sub_action = backend.show_subsection(
                    f"Disk layout — partitions for {disk.device or 'disk'}",
                    self._partition_section_items(partitions),
                )
                if sub_action == "done":
                    break

                if sub_action == "add":
                    avail = _available_partition_bytes(_disk_size_bytes(disk.device), partitions, None)
                    part_values = backend.show_form(
                        self._partition_form(
                            PartitionConfig(),
                            len(partitions) + 1,
                            existing=False,
                            available_bytes=avail,
                            partition_table=disk.partition_table,
                            boot_mode=disk.boot_mode,
                        )
                    )
                    if part_values is None or part_values.get("__action__") == "delete":
                        continue
                    partitions.append(self._partition_from_values(part_values))
                    continue

                if sub_action.startswith("edit:"):
                    try:
                        edit_idx = int(sub_action.split(":", 1)[1])
                    except ValueError:
                        continue
                    if edit_idx < 0 or edit_idx >= len(partitions):
                        continue

                    avail = _available_partition_bytes(_disk_size_bytes(disk.device), partitions, edit_idx)
                    part_values = backend.show_form(
                        self._partition_form(
                            partitions[edit_idx],
                            edit_idx + 1,
                            existing=True,
                            available_bytes=avail,
                            partition_table=disk.partition_table,
                            boot_mode=disk.boot_mode,
                        )
                    )
                    if part_values is None:
                        continue
                    if part_values.get("__action__") == "delete":
                        partitions.pop(edit_idx)
                    else:
                        partitions[edit_idx] = self._partition_from_values(part_values)

        disk.partitions = partitions
        config.disks = [disk]
        return config
