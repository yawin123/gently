"""
Contract tests for installer.partition.

Run with:
    python3 tests/test_partition.py
"""

from __future__ import annotations

import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "vendor"))

import i18n as _i18n

from installer.partition import (
    FALLBACK_DISK_BYTES,
    FSTAB_STAGING_PATH,
    PartitionError,
    _compute_positions,
    _generate_fstab_lines,
    _parse_size_bytes,
    _partition_device,
    _sorted_mountable,
    execute,
)
from installer.runner import CommandExecutionError, CommandResult, CommandSpec
from model.config import DiskConfig, GentlyConfig, PartitionConfig


# ---------------------------------------------------------------------------
# Minimal fake runner
# ---------------------------------------------------------------------------

class _FakeRunner:
    transport = "local"

    def __init__(
        self,
        *,
        disk_size_bytes: int = 20 * 1024 ** 3,
        uuids: dict[str, str] | None = None,
        dry_run: bool = False,
    ):
        self.disk_size = disk_size_bytes
        self.uuids = uuids or {}
        self.dry_run = dry_run
        self.shell_commands: list[str] = []
        self.run_specs: list[CommandSpec] = []
        self.confirm_callback = None  # matches Runner interface

    def run_shell(
        self,
        command: str,
        check: bool = True,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        phase: str | None = None,
    ) -> CommandResult:
        self.shell_commands.append(command)
        rc = 0
        out = ""
        err = ""

        if "lsblk -b -n -o SIZE" in command:
            out = f"{self.disk_size}\n"
        elif command.startswith("blkid -s UUID -o value "):
            dev = command.split()[-1].strip("'\"")
            uuid = self.uuids.get(dev, "")
            out = uuid + "\n" if uuid else ""
            rc = 0 if uuid else 1

        result = CommandResult(
            argv=["bash", "-lc", command],
            returncode=rc,
            stdout=out,
            stderr=err,
            duration_sec=0.0,
            transport=self.transport,
            phase=phase,
        )
        if check and rc != 0:
            raise CommandExecutionError(
                CommandSpec(argv=result.argv, check=check, phase=phase), result
            )
        return result

    def run(self, spec: CommandSpec) -> CommandResult:
        self.run_specs.append(spec)
        return CommandResult(
            argv=spec.argv,
            returncode=0,
            stdout="",
            stderr="",
            duration_sec=0.0,
            transport=self.transport,
            phase=spec.phase,
        )

    def confirm(self, message: str, yes_key: str = "ui_yes", no_key: str = "ui_no") -> bool:
        if self.dry_run or self.confirm_callback is None:
            return True
        return self.confirm_callback(message, yes_key, no_key)

    # Cleanup stack — mirrors the Runner interface so partition tests keep working.
    def push_cleanup(self, description: str, action) -> None:
        pass  # no-op in tests: we don't need to verify cleanup registration here

    def pop_cleanup(self):
        return None

    def run_cleanup(self):
        return []


# ---------------------------------------------------------------------------
# Unit tests — pure helpers
# ---------------------------------------------------------------------------

def test_partition_device_sata():
    assert _partition_device("/dev/sda", 1) == "/dev/sda1"
    assert _partition_device("/dev/sda", 3) == "/dev/sda3"
    print("PASS  partition_device SATA naming")


def test_partition_device_nvme():
    assert _partition_device("/dev/nvme0n1", 1) == "/dev/nvme0n1p1"
    assert _partition_device("/dev/nvme0n1", 2) == "/dev/nvme0n1p2"
    print("PASS  partition_device NVMe naming")


def test_partition_device_loop():
    assert _partition_device("/dev/loop0", 1) == "/dev/loop0p1"
    print("PASS  partition_device loop naming")


def test_parse_size_bytes_absolute():
    disk = 100 * 1024 ** 3
    assert _parse_size_bytes("512M", disk) == 512 * 1024 ** 2
    assert _parse_size_bytes("20G", disk) == 20 * 1024 ** 3
    assert _parse_size_bytes("1T", disk) == 1024 ** 4
    print("PASS  parse_size_bytes absolute")


def test_parse_size_bytes_percentage():
    disk = 100 * 1024 ** 3  # 100 GiB
    result = _parse_size_bytes("50%", disk)
    assert result == 50 * 1024 ** 3, result
    print("PASS  parse_size_bytes percentage")


def test_parse_size_bytes_raw():
    disk = 100 * 1024 ** 3
    assert _parse_size_bytes("1073741824", disk) == 1024 ** 3
    print("PASS  parse_size_bytes raw bytes")


def test_parse_size_bytes_invalid():
    try:
        _parse_size_bytes("bad_value", 100 * 1024 ** 3)
    except PartitionError:
        print("PASS  parse_size_bytes rejects invalid expression")
        return
    raise AssertionError("Expected PartitionError")


def test_compute_positions_two_partitions():
    disk = 10 * 1024 ** 3  # 10 GiB
    parts = [
        PartitionConfig(label="boot", size="512M", filesystem="vfat"),
        PartitionConfig(label="root", size="9G",   filesystem="ext4"),
    ]
    positions = _compute_positions(parts, disk)
    assert len(positions) == 2
    start0, end0 = positions[0]
    start1, end1 = positions[1]
    assert start0 == "1MiB", start0
    assert end0 != "100%", "Only the last partition should use 100%"
    assert start1 == end0, f"Second partition must start where first ends: {start1} vs {end0}"
    assert end1 == "100%", end1
    print("PASS  compute_positions two partitions")


def test_compute_positions_last_fills_disk():
    disk = 20 * 1024 ** 3
    parts = [
        PartitionConfig(label="boot", size="512M", filesystem="vfat"),
        PartitionConfig(label="root", size="18G",  filesystem="ext4"),
    ]
    positions = _compute_positions(parts, disk)
    assert positions[-1][1] == "100%"
    print("PASS  compute_positions last partition fills disk")


def test_sorted_mountable_root_first():
    disk = DiskConfig(
        device="/dev/sda",
        partitions=[
            PartitionConfig(label="boot", filesystem="vfat",  mount="/boot"),
            PartitionConfig(label="swap", filesystem="swap",  mount=None),
            PartitionConfig(label="root", filesystem="ext4",  mount="/"),
            PartitionConfig(label="home", filesystem="ext4",  mount="/home"),
        ],
    )
    order = _sorted_mountable(disk)
    mounts = [p.mount for _, p in order]
    assert mounts[0] == "/", mounts
    # swap must not appear
    assert None not in mounts
    # /boot and /home come after /
    assert mounts.index("/") < mounts.index("/boot")
    print("PASS  sorted_mountable root-first ordering")


# ---------------------------------------------------------------------------
# fstab generation
# ---------------------------------------------------------------------------

def test_generate_fstab_uses_uuid_when_available():
    disk = DiskConfig(
        device="/dev/sda",
        partitions=[
            PartitionConfig(label="root", filesystem="ext4", mount="/"),
        ],
    )
    runner = _FakeRunner(uuids={"/dev/sda1": "aaaabbbb-1234-5678-90ab-cdef01234567"})
    fstab = _generate_fstab_lines(disk, runner)
    assert "UUID=aaaabbbb" in fstab, fstab
    assert "/" in fstab
    print("PASS  fstab uses UUID when available")


def test_generate_fstab_falls_back_to_device_path():
    disk = DiskConfig(
        device="/dev/sda",
        partitions=[
            PartitionConfig(label="root", filesystem="ext4", mount="/"),
        ],
    )
    runner = _FakeRunner(uuids={})  # blkid returns nothing
    fstab = _generate_fstab_lines(disk, runner)
    assert "/dev/sda1" in fstab, fstab
    print("PASS  fstab falls back to device path when UUID unavailable")


def test_generate_fstab_swap_entry():
    disk = DiskConfig(
        device="/dev/sda",
        partitions=[
            PartitionConfig(label="swap", filesystem="swap"),
        ],
    )
    runner = _FakeRunner(uuids={})
    fstab = _generate_fstab_lines(disk, runner)
    assert "swap" in fstab
    assert "none" in fstab
    print("PASS  fstab swap entry")


def test_generate_fstab_root_pass_1_others_pass_2():
    disk = DiskConfig(
        device="/dev/sda",
        partitions=[
            PartitionConfig(label="root", filesystem="ext4", mount="/"),
            PartitionConfig(label="boot", filesystem="vfat", mount="/boot"),
        ],
    )
    runner = _FakeRunner(uuids={})
    fstab = _generate_fstab_lines(disk, runner)
    lines = [l for l in fstab.splitlines() if not l.startswith("#") and l.strip()]
    # root → pass 1, /boot → pass 2
    root_line = next(l for l in lines if "\t/\t" in l)
    boot_line = next(l for l in lines if "\t/boot\t" in l)
    assert root_line.endswith("\t0\t1"), root_line
    assert boot_line.endswith("\t0\t2"), boot_line
    print("PASS  fstab pass values (root=1, others=2)")


# ---------------------------------------------------------------------------
# execute() integration
# ---------------------------------------------------------------------------

def _typical_disk() -> DiskConfig:
    return DiskConfig(
        device="/dev/sda",
        partition_table="gpt",
        boot_mode="uefi",
        partitions=[
            PartitionConfig(
                label="esp",
                size="512M",
                filesystem="vfat",
                mount="/boot/efi",
                flags=["esp"],
            ),
            PartitionConfig(
                label="swap",
                size="4G",
                filesystem="swap",
            ),
            PartitionConfig(
                label="root",
                size="14G",
                filesystem="ext4",
                mount="/",
            ),
        ],
    )


def test_execute_creates_partition_table():
    config = GentlyConfig(disks=[_typical_disk()])
    runner = _FakeRunner()
    execute(config, runner)
    assert any("mklabel gpt" in cmd for cmd in runner.shell_commands), runner.shell_commands
    print("PASS  execute creates partition table")


def test_execute_creates_three_partitions():
    config = GentlyConfig(disks=[_typical_disk()])
    runner = _FakeRunner()
    execute(config, runner)
    mkpart_cmds = [cmd for cmd in runner.shell_commands if "mkpart" in cmd]
    assert len(mkpart_cmds) == 3, mkpart_cmds
    print("PASS  execute creates three partitions")


def test_execute_sets_esp_flag():
    config = GentlyConfig(disks=[_typical_disk()])
    runner = _FakeRunner()
    execute(config, runner)
    assert any("set 1 esp on" in cmd for cmd in runner.shell_commands), runner.shell_commands
    print("PASS  execute sets ESP flag on first partition")


def test_execute_formats_vfat_ext4_swap():
    config = GentlyConfig(disks=[_typical_disk()])
    runner = _FakeRunner()
    execute(config, runner)
    cmds = runner.shell_commands
    assert any("mkfs.vfat" in c for c in cmds), cmds
    assert any("mkswap" in c for c in cmds), cmds
    assert any("mkfs.ext4" in c for c in cmds), cmds
    print("PASS  execute formats vfat, swap, and ext4 partitions")


def test_execute_mounts_root_before_subdirs():
    config = GentlyConfig(disks=[_typical_disk()])
    runner = _FakeRunner()
    execute(config, runner)
    mount_cmds = [cmd for cmd in runner.shell_commands if cmd.startswith("mount ")]
    # root must be mounted before /boot/efi
    root_idx = next((i for i, c in enumerate(mount_cmds) if "'/'" in c or " / " in c or c.endswith("/")), None)
    efi_idx  = next((i for i, c in enumerate(mount_cmds) if "efi" in c), None)
    assert root_idx is not None, mount_cmds
    assert efi_idx is not None, mount_cmds
    assert root_idx < efi_idx, f"root mounted at {root_idx}, /boot/efi at {efi_idx}"
    print("PASS  execute mounts root before subdirectories")


def test_execute_activates_swap():
    config = GentlyConfig(disks=[_typical_disk()])
    runner = _FakeRunner()
    execute(config, runner)
    assert any("swapon" in cmd for cmd in runner.shell_commands), runner.shell_commands
    print("PASS  execute activates swap")


def test_execute_writes_fstab():
    config = GentlyConfig(disks=[_typical_disk()])
    runner = _FakeRunner()
    execute(config, runner)
    fstab_specs = [
        s for s in runner.run_specs
        if len(s.argv) >= 4 and s.argv[3] == FSTAB_STAGING_PATH
    ]
    assert fstab_specs, "Expected a fstab write CommandSpec"
    print("PASS  execute writes fstab via runner.run()")


def test_execute_raises_on_missing_device():
    config = GentlyConfig(disks=[DiskConfig(device=None)])
    runner = _FakeRunner()
    try:
        execute(config, runner)
    except PartitionError as exc:
        assert str(exc) == _i18n.t("partition_error_disk_missing")
        print("PASS  execute raises PartitionError on missing device")
        return
    raise AssertionError("Expected PartitionError")


def test_execute_nvme_device_naming():
    disk = DiskConfig(
        device="/dev/nvme0n1",
        partition_table="gpt",
        partitions=[
            PartitionConfig(label="esp",  size="512M", filesystem="vfat",  mount="/boot", flags=["esp"]),
            PartitionConfig(label="root", size="99G",  filesystem="ext4",  mount="/"),
        ],
    )
    config = GentlyConfig(disks=[disk])
    runner = _FakeRunner()
    execute(config, runner)
    # Partition devices must use 'p' separator
    assert any("nvme0n1p1" in cmd for cmd in runner.shell_commands), runner.shell_commands
    assert any("nvme0n1p2" in cmd for cmd in runner.shell_commands), runner.shell_commands
    print("PASS  execute uses NVMe 'p' partition naming")


def test_execute_dry_run_skips_disk_query():
    config = GentlyConfig(disks=[_typical_disk()])
    runner = _FakeRunner(dry_run=True)
    execute(config, runner)
    # In dry-run, lsblk should NOT be called (FALLBACK_DISK_BYTES is used instead)
    assert not any("lsblk" in cmd for cmd in runner.shell_commands), runner.shell_commands
    print("PASS  execute does not call lsblk in dry-run mode")


# ---------------------------------------------------------------------------
# confirm_wipe
# ---------------------------------------------------------------------------

def test_execute_asks_confirm_when_confirm_wipe_true():
    config = GentlyConfig(disks=[_typical_disk()])  # confirm_wipe defaults to True
    confirmations: list[str] = []

    runner = _FakeRunner()
    runner.confirm_callback = lambda msg, _yes, _no: (confirmations.append(msg), True)[1]

    execute(config, runner)
    assert len(confirmations) == 1
    assert "/dev/sda" in confirmations[0]
    print("PASS  execute asks confirm when confirm_wipe=True")


def test_execute_skips_confirm_when_confirm_wipe_false():
    disk = _typical_disk()
    disk.confirm_wipe = False
    config = GentlyConfig(disks=[disk])
    confirmations: list[str] = []

    runner = _FakeRunner()
    runner.confirm_callback = lambda msg, _yes, _no: (confirmations.append(msg), True)[1]

    execute(config, runner)
    assert len(confirmations) == 0
    print("PASS  execute skips confirm when confirm_wipe=False")


def test_execute_aborts_when_user_denies_confirm():
    config = GentlyConfig(disks=[_typical_disk()])
    runner = _FakeRunner()
    runner.confirm_callback = lambda _msg, _yes, _no: False

    try:
        execute(config, runner)
    except PartitionError as exc:
        assert "cancelled" in str(exc).lower() or "cancelado" in str(exc).lower()
        # No parted commands should have been issued
        assert not any("mklabel" in cmd for cmd in runner.shell_commands), runner.shell_commands
        print("PASS  execute aborts without touching disk when user denies confirm")
        return
    raise AssertionError("Expected PartitionError")


def test_execute_auto_confirms_in_dry_run():
    config = GentlyConfig(disks=[_typical_disk()])
    denied_calls: list[str] = []

    runner = _FakeRunner(dry_run=True)
    # callback always returns False — should never be called in dry_run
    runner.confirm_callback = lambda msg, _yes, _no: (denied_calls.append(msg), False)[1]

    execute(config, runner)
    assert len(denied_calls) == 0
    print("PASS  execute auto-confirms in dry-run (callback not called)")


if __name__ == "__main__":
    test_partition_device_sata()
    test_partition_device_nvme()
    test_partition_device_loop()
    test_parse_size_bytes_absolute()
    test_parse_size_bytes_percentage()
    test_parse_size_bytes_raw()
    test_parse_size_bytes_invalid()
    test_compute_positions_two_partitions()
    test_compute_positions_last_fills_disk()
    test_sorted_mountable_root_first()
    test_generate_fstab_uses_uuid_when_available()
    test_generate_fstab_falls_back_to_device_path()
    test_generate_fstab_swap_entry()
    test_generate_fstab_root_pass_1_others_pass_2()
    test_execute_creates_partition_table()
    test_execute_creates_three_partitions()
    test_execute_sets_esp_flag()
    test_execute_formats_vfat_ext4_swap()
    test_execute_mounts_root_before_subdirs()
    test_execute_activates_swap()
    test_execute_writes_fstab()
    test_execute_raises_on_missing_device()
    test_execute_nvme_device_naming()
    test_execute_dry_run_skips_disk_query()
    test_execute_asks_confirm_when_confirm_wipe_true()
    test_execute_skips_confirm_when_confirm_wipe_false()
    test_execute_aborts_when_user_denies_confirm()
    test_execute_auto_confirms_in_dry_run()
    print()
    print("All partition tests passed.")
