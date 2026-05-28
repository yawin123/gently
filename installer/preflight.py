from __future__ import annotations

import re
import shlex

from model.config import GentlyConfig
from util.parse import parse_int

from installer.runner import CommandSpec, Runner, RunnerError


PHASE_KEY = "preflight"
DISTCC_DEFAULT_PORT = 3632
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
		runner.run_shell("sleep 5", phase=PHASE_KEY)


def _check_connectivity(runner: Runner) -> None:
	runner.run_shell(
		"ping -c 1 8.8.8.8 >/dev/null",
		phase=PHASE_KEY,
	)
	runner.run_shell("bash /tmp/tmp.sh", phase=PHASE_KEY, check=False)


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
	stage3 = config.stage3
	if stage3 is None or not stage3.local_path:
		return

	local_path = shlex.quote(stage3.local_path)
	runner.run_shell(f"test -r {local_path}", phase=PHASE_KEY)


def _download_file(url: str, dest: str, runner: Runner) -> None:
	runner.run_shell(
		f"python3 -c \""
		f"import urllib.request; "
		f"urllib.request.urlretrieve({url!r}, {dest!r})"
		f"\"",
		phase=PHASE_KEY,
	)


def _verify_signature(tarball: str, sig_path: str, runner: Runner) -> None:
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


def _ensure_stage3_available(config: GentlyConfig, runner: Runner) -> None:
	stage3 = config.stage3
	if stage3 is None:
		return

	# If a local path is specified, just verify it (already done in _check_stage3_local_path).
	if stage3.local_path:
		return

	# Dry-run: skip network downloads entirely so planning tests stay fast.
	if runner.dry_run:
		return

	# If a tarball is already cached, skip download.
	stdout = runner.run_shell(
		f'test -s {shlex.quote(STAGE3_CACHE)} && echo yes || echo no',
		check=False,
		phase=PHASE_KEY,
	).stdout.strip()
	if stdout == "yes":
		config.stage3.local_path = STAGE3_CACHE
		return

	# Resolve the source URL.
	if stage3.tarball_url:
		url = stage3.tarball_url
	else:
		mirror = (stage3.mirror or "https://distfiles.gentoo.org").rstrip("/")
		arch = stage3.arch or "amd64"
		variant = stage3.variant or "openrc"
		url = _autobuilds_latest_url(mirror, arch, variant, runner)

	_download_file(url, STAGE3_CACHE, runner)

	# Verify signature if requested.
	if stage3.verify_signature:
		if stage3.signature_url:
			sig_url = stage3.signature_url
		elif stage3.signature_path:
			runner.run_shell(f"test -r {shlex.quote(stage3.signature_path)}", phase=PHASE_KEY)
			_verify_signature(STAGE3_CACHE, stage3.signature_path, runner)
			config.stage3.local_path = STAGE3_CACHE
			return
		else:
			sig_url = url + ".asc"
		sig_path = STAGE3_CACHE + ".asc"
		_download_file(sig_url, sig_path, runner)
		_verify_signature(STAGE3_CACHE, sig_path, runner)

	config.stage3.local_path = STAGE3_CACHE


def _parse_distcc_host(spec: str) -> str:
	base = spec.split("/", 1)[0]
	base = base.split(",", 1)[0]
	return base.strip()


def _check_distcc_hosts(config: GentlyConfig, runner: Runner) -> None:
	distcc = config.distcc
	if distcc is None or not distcc.enabled:
		return

	# Distcc must be available in the live environment to offload compilations.
	try:
		runner.run_shell("command -v distcc >/dev/null", phase=PHASE_KEY)
	except RunnerError as exc:
		raise PreflightError(
			"distcc is enabled but the distcc client is not installed in the live environment. "
			"Bootstrap it first (sys-devel/distcc) before running the installer."
		) from exc

	hosts = list(distcc.hosts or [])
	if not hosts:
		raise PreflightError("distcc.enabled=true but distcc.hosts is empty")

	port = distcc.port or DISTCC_DEFAULT_PORT
	for host_spec in hosts:
		host = _parse_distcc_host(host_spec)
		if not host:
			raise PreflightError(f"Invalid distcc host entry: {host_spec!r}")

		runner.run(
			CommandSpec(
				argv=[
					"python3",
					"-c",
					(
						"import socket,sys; "
						"socket.create_connection((sys.argv[1], int(sys.argv[2])), 2).close()"
					),
					host,
					str(port),
				],
				phase=PHASE_KEY,
			)
		)


def execute(config: GentlyConfig, runner: Runner) -> None:
	try:
		_check_required_commands(runner)
		_check_connectivity(runner)
		_check_disks(config, runner)
		_check_stage3_local_path(config, runner)
		_ensure_stage3_available(config, runner)
		_check_distcc_hosts(config, runner)
	except RunnerError as exc:
		raise PreflightError(str(exc)) from exc
