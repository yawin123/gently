from __future__ import annotations

import i18n
import re
import shlex
from typing import Sequence

from model.config import DiskConfig, GentlyConfig, PartitionConfig
from installer.runner import CommandSpec, Runner, RunnerError
from util.parse import parse_int

PHASE_KEY = "partition"
MOUNTPOINT = "/mnt/gentoo"
# Staging path for the generated fstab.  /mnt/gentoo/etc/ does not exist
# until stage3 is extracted; stage3.execute() copies this file there.
FSTAB_STAGING_PATH = "/tmp/gently-fstab"
START_MIB = 1          # standard 1 MiB alignment offset from disk start
_MiB = 1024 * 1024

# Used when the disk size cannot be determined (e.g. dry-run or virtual devices).
FALLBACK_DISK_BYTES = 500 * 1024 ** 3  # 500 GiB


class PartitionError(RunnerError):
	pass


# ---------------------------------------------------------------------------
# Device helpers
# ---------------------------------------------------------------------------

def _partition_device(disk_device: str, index: int) -> str:
	"""Return the partition device path for 1-based *index*.

	NVMe and loop devices already end in a digit, so the partition separator
	is 'p': /dev/nvme0n1 → /dev/nvme0n1p1, /dev/loop0 → /dev/loop0p1.
	SATA/SCSI/IDE disks use a plain numeric suffix: /dev/sda → /dev/sda1.
	"""
	if disk_device[-1].isdigit():
		return f"{disk_device}p{index}"
	return f"{disk_device}{index}"


# ---------------------------------------------------------------------------
# Size helpers
# ---------------------------------------------------------------------------

def _parse_size_bytes(size_expr: str, disk_bytes: int) -> int:
	"""Convert a size expression to bytes.

	Understands:
	  - Percentages: '50%'
	  - SI suffixes: '512M', '20G', '1T' (decimal or binary, case-insensitive)
	  - Raw bytes:   '536870912'
	"""
	s = size_expr.strip()
	if not s:
		raise PartitionError(i18n.t("partition_error_empty_size"))

	# Percentage
	m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*%", s)
	if m:
		return int(disk_bytes * float(m.group(1)) / 100.0)

	# SI suffix (allows optional 'i' and 'B' suffix: GiB, GB, G all OK)
	m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*([kmgtp])(?:i?b)?", s, re.IGNORECASE)
	if m:
		num = float(m.group(1))
		unit = m.group(2).upper()
		factor = {"K": 1024, "M": 1024 ** 2, "G": 1024 ** 3, "T": 1024 ** 4, "P": 1024 ** 5}[unit]
		return int(num * factor)

	# Raw bytes
	if re.fullmatch(r"\d+", s):
		return int(s)

	raise PartitionError(i18n.t("partition_error_unrecognised_size", expr=s))


def _bytes_to_mib(n: int) -> str:
	return f"{n // _MiB}MiB"


def _compute_positions(
	partitions: Sequence[PartitionConfig],
	disk_bytes: int,
) -> list[tuple[str, str]]:
	"""Return (start, end) MiB strings for each partition.

	The last partition extends to 100% of the disk so no space is wasted.
	"""
	positions: list[tuple[str, str]] = []
	current = START_MIB * _MiB

	for i, part in enumerate(partitions):
		if not part.size:
			raise PartitionError(i18n.t("partition_error_missing_size", n=i + 1, label=part.label))

		start_str = _bytes_to_mib(current)

		if i == len(partitions) - 1:
			# Last partition: fill the rest of the disk.
			end_str = "100%"
		else:
			size_bytes = _parse_size_bytes(part.size, disk_bytes)
			end_bytes = current + size_bytes
			end_str = _bytes_to_mib(end_bytes)
			current = end_bytes

		positions.append((start_str, end_str))

	return positions


def _query_disk_bytes(device: str, runner: Runner) -> int:
	"""Query the block device size in bytes via lsblk."""
	result = runner.run_shell(
		f"lsblk -b -n -o SIZE {shlex.quote(device)} | head -1",
		phase=PHASE_KEY,
	)
	if runner.dry_run:
		return FALLBACK_DISK_BYTES
	return parse_int(result.stdout, f"disk size for {device}", PartitionError)


# ---------------------------------------------------------------------------
# Partition table & partition creation
# ---------------------------------------------------------------------------

# Maps our filesystem names to the TYPE value blkid reports after mkfs.
_BLKID_FS_TYPE: dict[str, str] = {
	"vfat":  "vfat",
	"fat32": "vfat",
	"fat16": "vfat",
	"ext2":  "ext2",
	"ext3":  "ext3",
	"ext4":  "ext4",
	"btrfs": "btrfs",
	"xfs":   "xfs",
	"swap":  "swap",
}

_PARTED_FS_TYPE: dict[str, str] = {
	"vfat":  "fat32",
	"fat32": "fat32",
	"fat16": "fat16",
	"ext2":  "ext2",
	"ext3":  "ext3",
	"ext4":  "ext4",
	"btrfs": "btrfs",
	"xfs":   "xfs",
	"swap":  "linux-swap",
}


def _fs_type_for_parted(filesystem: str | None) -> str:
	return _PARTED_FS_TYPE.get((filesystem or "").lower(), "ext4")


def _create_partition_table(disk: DiskConfig, runner: Runner) -> None:
	device = shlex.quote(disk.device)  # type: ignore[arg-type]
	table = (disk.partition_table or "gpt").lower()
	runner.run_shell(
		f"parted -s {device} -- mklabel {shlex.quote(table)}",
		phase=PHASE_KEY,
	)


def _create_partitions(
	disk: DiskConfig,
	positions: list[tuple[str, str]],
	runner: Runner,
) -> None:
	device = shlex.quote(disk.device)  # type: ignore[arg-type]
	table = (disk.partition_table or "gpt").lower()

	for i, (part, (start, end)) in enumerate(zip(disk.partitions, positions), start=1):
		fs_type = _fs_type_for_parted(part.filesystem)
		label = part.label or f"part{i}"

		if table == "msdos":
			# MBR: first arg is partition type (primary/logical/extended)
			runner.run_shell(
				f"parted -s {device} -- mkpart primary {fs_type} {start} {end}",
				phase=PHASE_KEY,
			)
		else:
			# GPT: first arg is the partition name
			runner.run_shell(
				f"parted -s {device} -- mkpart {shlex.quote(label)} {fs_type} {start} {end}",
				phase=PHASE_KEY,
			)

		for flag in (part.flags or []):
			runner.run_shell(
				f"parted -s {device} -- set {i} {shlex.quote(flag)} on",
				phase=PHASE_KEY,
			)


# ---------------------------------------------------------------------------
# Filesystem formatting
# ---------------------------------------------------------------------------

def _format_partitions(disk: DiskConfig, runner: Runner) -> None:
	for i, part in enumerate(disk.partitions, start=1):
		pdev = _partition_device(disk.device, i)  # type: ignore[arg-type]
		fs = (part.filesystem or "").lower()
		label = part.label or f"part{i}"

		if fs in ("vfat", "fat32"):
			# FAT labels are max 11 characters.
			fat_label = label[:11]
			runner.run_shell(
				f"mkfs.vfat -F 32 -n {shlex.quote(fat_label)} {shlex.quote(pdev)}",
				phase=PHASE_KEY,
			)
		elif fs == "swap":
			runner.run_shell(
				f"mkswap -L {shlex.quote(label)} {shlex.quote(pdev)}",
				phase=PHASE_KEY,
			)
		elif fs == "btrfs":
			runner.run_shell(
				f"mkfs.btrfs -L {shlex.quote(label)} {shlex.quote(pdev)}",
				phase=PHASE_KEY,
			)
		elif fs == "xfs":
			runner.run_shell(
				f"mkfs.xfs -L {shlex.quote(label)} {shlex.quote(pdev)}",
				phase=PHASE_KEY,
			)
		elif fs in ("ext2", "ext3", "ext4"):
			runner.run_shell(
				f"mkfs.{fs} -L {shlex.quote(label)} {shlex.quote(pdev)}",
				phase=PHASE_KEY,
			)
		# filesystem=None or unrecognised → raw partition, no formatting


# ---------------------------------------------------------------------------
# Mounting
# ---------------------------------------------------------------------------

def _sorted_mountable(
	disk: DiskConfig,
) -> list[tuple[int, PartitionConfig]]:
	"""Return (1-based index, partition) pairs sorted root-first, then by path depth."""
	mountable = [
		(i + 1, part)
		for i, part in enumerate(disk.partitions)
		if part.mount and (part.filesystem or "").lower() not in ("swap", "")
	]
	mountable.sort(key=lambda x: (x[1].mount.count("/"), x[1].mount))  # type: ignore[union-attr]
	return mountable


def _mount_partitions(
	disk: DiskConfig,
	runner: Runner,
	mountpoint: str = MOUNTPOINT,
) -> None:
	for i, part in _sorted_mountable(disk):
		pdev = _partition_device(disk.device, i)  # type: ignore[arg-type]
		target = mountpoint + part.mount  # type: ignore[operator]
		opts = part.mount_options or "defaults"
		runner.run_shell(f"mkdir -p {shlex.quote(target)}", phase=PHASE_KEY)
		runner.run_shell(
			f"mount -o {shlex.quote(opts)} {shlex.quote(pdev)} {shlex.quote(target)}",
			phase=PHASE_KEY,
		)
		_target = target  # capture loop variable
		runner.push_cleanup(
			f"umount {_target}",
			lambda t=_target: runner.run_shell(f"umount -l {shlex.quote(t)}", check=False, phase="cleanup"),
		)


def _activate_swap(disk: DiskConfig, runner: Runner) -> None:
	for i, part in enumerate(disk.partitions, start=1):
		if (part.filesystem or "").lower() == "swap":
			pdev = _partition_device(disk.device, i)  # type: ignore[arg-type]
			runner.run_shell(f"swapon {shlex.quote(pdev)}", phase=PHASE_KEY)
			_pdev = pdev  # capture loop variable
			runner.push_cleanup(
				f"swapoff {_pdev}",
				lambda d=_pdev: runner.run_shell(f"swapoff {shlex.quote(d)}", check=False, phase="cleanup"),
			)


# ---------------------------------------------------------------------------
# fstab
# ---------------------------------------------------------------------------

def _get_uuid(device: str, runner: Runner) -> str | None:
	"""Query the filesystem UUID of *device* via blkid. Returns None on failure."""
	result = runner.run_shell(
		f"blkid -s UUID -o value {shlex.quote(device)}",
		check=False,
		phase=PHASE_KEY,
	)
	if runner.dry_run:
		return None
	uuid = result.stdout.strip()
	return uuid if uuid else None


def _generate_fstab_lines(disk: DiskConfig, runner: Runner) -> str:
	lines = [
		"# /etc/fstab — generated by gently",
		"# <device>              <mountpoint>  <type>       <options>  <dump>  <pass>",
		"",
	]
	for i, part in enumerate(disk.partitions, start=1):
		fs = (part.filesystem or "").lower()
		is_swap = fs == "swap"
		has_mount = bool(part.mount)

		if not is_swap and not has_mount:
			continue  # raw or unmounted partition

		pdev = _partition_device(disk.device, i)  # type: ignore[arg-type]
		uuid = _get_uuid(pdev, runner)
		device_spec = f"UUID={uuid}" if uuid else pdev

		if is_swap:
			lines.append(f"{device_spec}\tnone\tswap\tsw\t0\t0")
		else:
			opts = part.mount_options or "defaults"
			fstab_pass = "1" if part.mount == "/" else "2"
			lines.append(f"{device_spec}\t{part.mount}\t{fs}\t{opts}\t0\t{fstab_pass}")

	return "\n".join(lines) + "\n"


def _write_fstab(
	disk: DiskConfig,
	runner: Runner,
	mountpoint: str = MOUNTPOINT,
) -> None:
	del mountpoint  # unused: fstab is staged to FSTAB_STAGING_PATH, not written to the target
	fstab_content = _generate_fstab_lines(disk, runner)
	# Write to staging path — /mnt/gentoo/etc/ does not exist yet (stage3 not extracted).
	# stage3.execute() copies this file to /mnt/gentoo/etc/fstab after extraction.
	runner.run(
		CommandSpec(
			argv=[
				"python3", "-c",
				"import sys; open(sys.argv[1], 'w').write(sys.argv[2])",
				FSTAB_STAGING_PATH,
				fstab_content,
			],
			phase=PHASE_KEY,
		)
	)


# ---------------------------------------------------------------------------
# Post-partition verification
# ---------------------------------------------------------------------------

def _verify_partitions(
	disk: DiskConfig,
	runner: Runner,
	mountpoint: str = MOUNTPOINT,
) -> None:
	"""Verify that partitions exist, have the expected filesystem type, and are mounted."""
	# 1. Each partition device node must exist.
	for i, part in enumerate(disk.partitions, start=1):
		pdev = _partition_device(disk.device, i)  # type: ignore[arg-type]
		result = runner.run_shell(
			f"test -b {shlex.quote(pdev)}",
			check=False,
			phase=PHASE_KEY,
		)
		if result.returncode != 0:
			raise PartitionError(i18n.t("partition_error_missing_device", device=pdev))
	# 2. Each formatted partition must have the expected filesystem type.
	for i, part in enumerate(disk.partitions, start=1):
		fs = (part.filesystem or "").lower()
		if not fs:
			continue  # raw partition — skip
		expected = _BLKID_FS_TYPE.get(fs)
		if expected is None:
			continue  # unknown type — skip silently

		pdev = _partition_device(disk.device, i)  # type: ignore[arg-type]
		result = runner.run_shell(
			f"blkid -s TYPE -o value {shlex.quote(pdev)}",
			check=False,
			phase=PHASE_KEY,
		)
		actual = result.stdout.strip().lower()
		if actual and actual != expected:
			raise PartitionError(
					i18n.t("partition_error_fs_type_mismatch", device=pdev, expected=expected, actual=actual)
			)

	# 3. Each mounted partition must appear in the mount table.
	for i, part in _sorted_mountable(disk):
		target = mountpoint + part.mount  # type: ignore[operator]
		result = runner.run_shell(
			f"findmnt --noheadings {shlex.quote(target)}",
			check=False,
			phase=PHASE_KEY,
		)
		if result.returncode != 0:
			pdev = _partition_device(disk.device, i)  # type: ignore[arg-type]
			raise PartitionError(i18n.t("partition_error_not_mounted", target=target, device=pdev))
# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def execute(config: GentlyConfig, runner: Runner) -> None:
	for disk in config.disks:
		if not disk.device:
			raise PartitionError(i18n.t("partition_error_disk_missing"))

		if disk.confirm_wipe is not False:
			confirmed = runner.confirm(
					i18n.t("partition_confirm_wipe_msg", device=disk.device),
					"ui_confirm_yes",
					"ui_confirm_no",
			)
			if not confirmed:
				raise PartitionError(i18n.t("partition_error_cancelled", device=disk.device))

		disk_bytes = _query_disk_bytes(disk.device, runner)
		positions = _compute_positions(disk.partitions, disk_bytes)

		_create_partition_table(disk, runner)
		_create_partitions(disk, positions, runner)

		# Inform the kernel of the new partition table and wait for udev
		# to create the partition device nodes before attempting mkfs.
		runner.run_shell(f"partprobe {shlex.quote(disk.device)}", phase=PHASE_KEY)
		runner.run_shell("udevadm settle", phase=PHASE_KEY)

		_format_partitions(disk, runner)
		_mount_partitions(disk, runner)
		_activate_swap(disk, runner)
		_write_fstab(disk, runner)
		_verify_partitions(disk, runner)
