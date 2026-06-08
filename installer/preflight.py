from __future__ import annotations

import re
import shlex

from model.config import GentlyConfig, Stage3Config
from util.parse import parse_int

from installer.runner import CommandSpec, Runner, RunnerError


PHASE_KEY = "preflight"
STAGE3_CACHE = "/tmp/gently-stage3.tar.xz"
REQUIRED_COMMANDS = [
	"parted",
	"mkfs.ext4",
	"mkfs.vfat",
	"tar",
	"gpg",
]


class PreflightError(RunnerError):
	pass


def _check_required_commands(runner: Runner) -> None:
	for cmd in REQUIRED_COMMANDS:
		runner.run_shell(
			f"command -v {shlex.quote(cmd)} >/dev/null",
			phase=PHASE_KEY,
		)


def _check_connectivity(runner: Runner) -> None:
	runner.run_shell(
		"ping -c 1 8.8.8.8 >/dev/null",
		phase=PHASE_KEY,
	)


def _check_disks(config: GentlyConfig, runner: Runner) -> None:
	for disk in config.disks:
		if not disk.device:
			raise PreflightError("Disk device is missing in configuration")

		device = shlex.quote(disk.device)
		runner.run_shell(f"test -b {device}", phase=PHASE_KEY)

		mounted = runner.run_shell(
			f"lsblk -nro MOUNTPOINT {device} | sed '/^$/d'",
			check=False,
			phase=PHASE_KEY,
		)
		if mounted.returncode != 0:
			raise PreflightError(
				f"Could not inspect mountpoints for disk {disk.device}: {mounted.stderr.strip()}"
			)
		if mounted.stdout.strip():
			raise PreflightError(
				f"Disk {disk.device} has mounted filesystems and cannot be reused safely"
			)


def _check_stage3_local_path(config: GentlyConfig, runner: Runner) -> None:
	"""Verify stage3.local_path exists and is readable.

	If the path is set but unreadable, clear it so _ensure_stage3_available
	falls through to auto-download (or tarball_url) instead of crashing.
	"""
	stage3 = config.stage3
	if stage3 is None or not stage3.local_path:
		return

	local_path = shlex.quote(stage3.local_path)
	result = runner.run_shell(f"test -r {local_path}", check=False, phase=PHASE_KEY)
	if result.returncode != 0:
		# Path set but not available — clear it and let _ensure_stage3_available
		# download/cache the tarball automatically.
		stage3.local_path = None


def _download_file(url: str, dest: str, runner: Runner) -> None:
	"""Download *url* to *dest* using Python's stdlib (portable, no wget needed).

	Passes url and dest as separate argv entries after -c to avoid shell
	quoting issues across local and SSH transports.
	"""
	code = (
		"import urllib.request, sys; "
		"urllib.request.urlretrieve(sys.argv[1], sys.argv[2])"
	)
	runner.run(
		CommandSpec(
			argv=["python3", "-c", code, url, dest],
			check=True,
			phase=PHASE_KEY,
		)
	)


def _verify_signature(tarball: str, sig_path: str, runner: Runner) -> None:
	if runner.dry_run:
		return  # no real gpg in dry-run

	# First attempt: let gpg auto-retrieve the signing key.
	result = runner.run_shell(
		f"gpg --auto-key-retrieve --verify {shlex.quote(sig_path)} {shlex.quote(tarball)}",
		check=False,
		phase=PHASE_KEY,
	)
	if result.returncode == 0:
		return

	# If auto-retrieve failed, extract the key id from stderr and fetch manually.
	match = re.search(r"using RSA key ([0-9A-F]+)", result.stderr)
	if not match:
		raise PreflightError(f"GPG verification failed and could not determine signing key:\n{result.stderr.strip()}")

	key_id = match.group(1)
	runner.run_shell(
		f"gpg --keyserver hkps://keys.gentoo.org --recv-keys {key_id}",
		phase=PHASE_KEY,
	)
	runner.run_shell(
		f"gpg --verify {shlex.quote(sig_path)} {shlex.quote(tarball)}",
		phase=PHASE_KEY,
	)


def _autobuilds_latest_url(mirror: str, arch: str, variant: str, runner: Runner) -> str:
	index_url = f"{mirror}/releases/{arch}/autobuilds/latest-stage3-{arch}-{variant}.txt"
	_download_file(index_url, "/tmp/gently-stage3-latest.txt", runner)
	contents = runner.run_shell(
		"cat /tmp/gently-stage3-latest.txt",
		phase=PHASE_KEY,
	).stdout

	if runner.dry_run:
		return f"{mirror}/releases/{arch}/autobuilds/<stage3-{arch}-{variant}-latest.tar.xz>"

	for line in contents.splitlines():
		line = line.strip()
		if not line:
			continue
		if line.startswith("#"):
			continue
		# Skip PGP armor lines and header fields.
		if line.startswith("-----") or line.startswith("Hash:"):
			continue
		# Format: "20250101T170000Z/stage3-amd64-openrc-20250101T170000Z.tar.xz  123456789"
		rel_path = line.split()[0]
		return f"{mirror}/releases/{arch}/autobuilds/{rel_path}"

	raise PreflightError("Could not determine latest stage3 URL from autobuilds index")


def _is_cached(path: str, runner: Runner) -> bool:
	"""Return True if *path* exists and has non-zero size."""
	result = runner.run_shell(
		f'test -s {shlex.quote(path)} && echo yes || echo no',
		check=False,
		phase=PHASE_KEY,
	)
	return result.stdout.strip() == "yes"


def _resolve_stage3_url(stage3: Stage3Config, runner: Runner) -> str:
	"""Return the download URL for the stage3 tarball.

	Priority: tarball_url > autobuilds discovery.
	"""
	if stage3.tarball_url:
		return stage3.tarball_url
	mirror = (stage3.mirror or "https://distfiles.gentoo.org").rstrip("/")
	arch = stage3.arch or "amd64"
	variant = stage3.variant or "openrc"
	return _autobuilds_latest_url(mirror, arch, variant, runner)


def _ensure_signature(stage3: Stage3Config, url: str | None, runner: Runner) -> None:
	"""Verify the stage3 tarball GPG signature.

	Downloads the .asc file if neither signature_path nor signature_url
	is given — derives the URL from the tarball URL by appending '.asc'.
	"""
	sig_path = STAGE3_CACHE + ".asc"

	if stage3.signature_path:
		runner.run_shell(f"test -r {shlex.quote(stage3.signature_path)}", phase=PHASE_KEY)
		_verify_signature(STAGE3_CACHE, stage3.signature_path, runner)
		return

	if not _is_cached(sig_path, runner):
		if stage3.signature_url:
			sig_url = stage3.signature_url
		elif url:
			sig_url = url + ".asc"
		else:
			raise PreflightError(
				"Cannot verify signature: no tarball URL to derive .asc path from, "
				"and neither signature_url nor signature_path is configured"
			)
		_download_file(sig_url, sig_path, runner)

	_verify_signature(STAGE3_CACHE, sig_path, runner)


def _ensure_stage3_available(config: GentlyConfig, runner: Runner) -> None:
	"""Ensure the stage3 tarball is available locally.

	Resolution order:
	  1. If config.stage3.local_path is set → already verified by _check_stage3_local_path.
	  2. If the tarball is already cached at STAGE3_CACHE → re-verify GPG if needed.
	  3. Otherwise, download from tarball_url or auto-discover from Gentoo autobuilds.

	In dry-run mode the download is skipped entirely — no network calls,
	no file-system checks.
	"""
	stage3 = config.stage3
	if stage3 is None:
		return

	# Already available locally — skip.
	if stage3.local_path:
		return

	if runner.dry_run:
		# In dry-run, simulate a resolved path so the rest of the pipeline
		# prints realistic-looking commands rather than crashing on None.
		config.stage3.local_path = STAGE3_CACHE
		return

	url: str | None = None
	cached = _is_cached(STAGE3_CACHE, runner)

	if not cached:
		url = _resolve_stage3_url(stage3, runner)
		_download_file(url, STAGE3_CACHE, runner)

	# Re-verify even when cached (safety on idempotent runs).
	if stage3.verify_signature:
		_ensure_signature(stage3, url, runner)

	config.stage3.local_path = STAGE3_CACHE



def execute(config: GentlyConfig, runner: Runner) -> None:
	try:
		_check_required_commands(runner)
		_check_connectivity(runner)
		_check_disks(config, runner)
		_check_stage3_local_path(config, runner)
		_ensure_stage3_available(config, runner)
	except RunnerError as exc:
		raise PreflightError(str(exc)) from exc
