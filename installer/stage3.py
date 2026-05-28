from __future__ import annotations

import i18n
import shlex

from model.config import GentlyConfig

from installer.partition import FSTAB_STAGING_PATH, MOUNTPOINT
from installer.preflight import PreflightError
from installer.runner import Runner, RunnerError
from util.parse import parse_int


PHASE_KEY = "stage3"


class Stage3Error(RunnerError):
	pass


MIN_STAGE3_FREE_BYTES = 2 * 1024 * 1024 * 1024


def _format_bytes_gib(value: int) -> str:
	return f"{value / (1024 ** 3):.2f} GiB"


def _required_stage3_space_bytes(config: GentlyConfig, runner: Runner) -> int:
	stage3 = config.stage3
	if stage3 is None or not stage3.local_path:
		return MIN_STAGE3_FREE_BYTES

	local_path = shlex.quote(stage3.local_path)
	runner.run_shell(f"test -r {local_path}", phase="stage3")

	size_result = runner.run_shell(
		f"stat -c %s {local_path}",
		phase="stage3",
	)
	file_size = parse_int(size_result.stdout, "stage3 local tarball size", PreflightError)
	return max(MIN_STAGE3_FREE_BYTES, file_size * 3)


def ensure_stage3_space(config: GentlyConfig, runner: Runner, mountpoint: str = "/mnt/gentoo") -> None:
	if runner.dry_run:
		return
	free_result = runner.run_shell(
		f'python3 -c "import shutil; print(shutil.disk_usage({mountpoint!r}).free)"',
		phase="stage3",
	)
	free_bytes = parse_int(free_result.stdout, f"free space in {mountpoint}", PreflightError)
	required = _required_stage3_space_bytes(config, runner)
	if free_bytes < required:
		raise PreflightError(
			f"Not enough free space in {mountpoint} for stage3 extraction. "
			f"Available={_format_bytes_gib(free_bytes)}, required={_format_bytes_gib(required)}"
		)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def execute(config: GentlyConfig, runner: Runner) -> None:
	stage3 = config.stage3
	if stage3 is None or not stage3.local_path:
		raise Stage3Error(i18n.t("stage3_error_no_tarball"))

	ensure_stage3_space(config, runner, MOUNTPOINT)

	tarball = shlex.quote(stage3.local_path)
	runner.run_shell(
		f"tar xpvf {tarball} -C {shlex.quote(MOUNTPOINT)}"
		f" --xattrs-include='*.*' --numeric-owner",
		phase=PHASE_KEY,
	)

	# Now that /mnt/gentoo/etc/ exists, place the fstab that was staged during
	# the partition phase (written to FSTAB_STAGING_PATH to avoid the chicken-and-egg
	# problem of /etc/ not existing before stage3 extraction).
	runner.run_shell(
		f"cp {shlex.quote(FSTAB_STAGING_PATH)} {shlex.quote(MOUNTPOINT)}/etc/fstab",
		phase=PHASE_KEY,
	)

