"""Contract tests for installer.portage — distcc setup step.

Run with:
    python3 tests/test_portage.py
"""
from __future__ import annotations

import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "vendor"))

from installer.portage import MOUNTPOINT, _apply_profile, _setup_cpu_flags, _setup_distcc, _setup_portage, _write_makeconf, execute
from installer.runner import CommandResult, CommandSpec
from model.config import DistccConfig, GentlyConfig, PortageBinrepoConfig, PortageConfig, PortageProfileConfig, Stage3Config, SystemConfig


# ---------------------------------------------------------------------------
# Fake runner — records chroot commands and host commands separately
# ---------------------------------------------------------------------------

class _FakeRunner:
	transport = "local"

	def __init__(self, *, dry_run: bool = False):
		self.dry_run = dry_run
		# Commands from run_shell() — run inside the chroot
		self.shell_commands: list[str] = []
		# Commands from run_host_shell() — run on the host
		self.host_commands: list[str] = []
		self.cleanup_stack: list[tuple[str, object]] = []
		# Simulate state after chroot_prep
		self.chroot_path: str | None = MOUNTPOINT
		self.log_callback = None
		self.confirm_callback = None

	def _result(self, argv: list[str], phase) -> CommandResult:
		return CommandResult(
			argv=argv, returncode=0, stdout="", stderr="",
			duration_sec=0.0, transport=self.transport, phase=phase,
		)

	def run(self, spec: CommandSpec):
		"""Simulate a CommandSpec execution (used by distcc TCP checks)."""
		# Simulate the python3 TCP check as a host command for review
		self.host_commands.append(" ".join(spec.argv))
		return self._result(spec.argv, spec.phase)

	# Map command substrings → stdout override, used to simulate eselect output.
	stdout_map: dict[str, str] = {}

	def run_shell(self, command: str, check: bool = True, cwd=None, env=None, phase=None, chroot: bool = False):
		if chroot:
			self.shell_commands.append(command)
		else:
			self.host_commands.append(command)
		stdout = next((v for k, v in self.stdout_map.items() if k in command), "")
		return CommandResult(
			argv=["bash", "-lc", command], returncode=0, stdout=stdout, stderr="",
			duration_sec=0.0, transport=self.transport, phase=phase,
		)

	def push_cleanup(self, description: str, action) -> None:
		self.cleanup_stack.append((description, action))

	def pop_cleanup(self):
		return self.cleanup_stack.pop() if self.cleanup_stack else None

	def run_cleanup(self):
		self.chroot_path = None
		errors = []
		while self.cleanup_stack:
			desc, action = self.cleanup_stack.pop()
			try:
				action()
			except Exception as exc:
				errors.append((desc, exc))
		return errors


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _cfg_distcc(**kwargs) -> GentlyConfig:
	return GentlyConfig(distcc=DistccConfig(**kwargs))


# ---------------------------------------------------------------------------
# Profile selection tests
# ---------------------------------------------------------------------------

_FAKE_PROFILE_LIST = """
  [1]   default/linux/amd64/23.0 (stable) *
  [2]   default/linux/amd64/23.0/systemd (stable)
  [3]   default/linux/amd64/23.0/desktop (stable)
"""


def test_profile_no_config_skips_eselect():
	"""No profile in config → stage3 default left untouched, eselect never called."""
	runner = _FakeRunner()
	_apply_profile(GentlyConfig(), runner)
	eselect_cmds = [c for c in runner.shell_commands if "eselect profile" in c]
	assert not eselect_cmds, f"eselect should not run when profile is absent: {eselect_cmds}"
	print("PASS  no profile in config → eselect skipped")


def test_profile_explicit_valid_applies():
	"""Explicit profile found in eselect list → applied."""
	runner = _FakeRunner()
	runner.stdout_map["eselect profile list"] = _FAKE_PROFILE_LIST
	cfg = GentlyConfig(portage=PortageConfig(profile=PortageProfileConfig(name="default/linux/amd64/23.0")))
	_apply_profile(cfg, runner)
	set_cmds = [c for c in runner.shell_commands if "eselect profile set" in c]
	assert len(set_cmds) == 1, f"Expected exactly one 'eselect profile set': {runner.shell_commands}"
	assert "default/linux/amd64/23.0" in set_cmds[0], set_cmds[0]
	print("PASS  explicit valid profile → eselect profile set called")


def test_profile_explicit_invalid_raises():
	"""Explicit profile NOT in eselect list → PortageError raised."""
	runner = _FakeRunner()
	runner.stdout_map["eselect profile list"] = _FAKE_PROFILE_LIST
	cfg = GentlyConfig(portage=PortageConfig(profile=PortageProfileConfig(name="default/linux/amd64/99.0")))
	try:
		_apply_profile(cfg, runner)
		assert False, "Expected PortageError"
	except Exception as exc:
		assert "default/linux/amd64/99.0" in str(exc), str(exc)
	print("PASS  unknown profile → PortageError with profile name in message")


def test_profile_validation_before_set():
	"""eselect profile list must be called before eselect profile set."""
	runner = _FakeRunner()
	runner.stdout_map["eselect profile list"] = _FAKE_PROFILE_LIST
	cfg = GentlyConfig(portage=PortageConfig(profile=PortageProfileConfig(name="default/linux/amd64/23.0")))
	_apply_profile(cfg, runner)
	cmds = runner.shell_commands
	list_idx = next(i for i, c in enumerate(cmds) if "eselect profile list" in c)
	set_idx  = next(i for i, c in enumerate(cmds) if "eselect profile set" in c)
	assert list_idx < set_idx, f"list (idx={list_idx}) must precede set (idx={set_idx})"
	print("PASS  eselect profile list called before eselect profile set")


def test_profile_set_runs_after_webrsync():
	"""eselect profile set must follow emerge-webrsync in the full portage setup."""
	runner = _FakeRunner()
	runner.stdout_map["eselect profile list"] = _FAKE_PROFILE_LIST
	cfg = GentlyConfig(portage=PortageConfig(profile=PortageProfileConfig(name="default/linux/amd64/23.0")))
	_setup_portage(cfg, runner)
	cmds = runner.shell_commands
	webrsync_idx = next(i for i, c in enumerate(cmds) if "emerge-webrsync" in c)
	set_idx      = next(i for i, c in enumerate(cmds) if "eselect profile set" in c)
	assert set_idx > webrsync_idx, "eselect profile set must run after emerge-webrsync"
	print("PASS  eselect profile set runs after emerge-webrsync")


# ---------------------------------------------------------------------------
# Timezone tests
# ---------------------------------------------------------------------------

def test_timezone_creates_symlink():
	runner = _FakeRunner()
	_setup_portage(GentlyConfig(system=SystemConfig(timezone="Europe/Madrid")), runner)
	ln_cmds = [c for c in runner.shell_commands if "ln -sf" in c and "/etc/localtime" in c]
	assert len(ln_cmds) == 1, f"Expected exactly one ln -sf /etc/localtime: {runner.shell_commands}"
	assert "../usr/share/zoneinfo/Europe/Madrid" in ln_cmds[0], ln_cmds[0]
	print("PASS  timezone creates relative symlink to /etc/localtime inside chroot")


def test_timezone_symlink_uses_relative_path():
	runner = _FakeRunner()
	_setup_portage(GentlyConfig(system=SystemConfig(timezone="America/New_York")), runner)
	ln_cmd = next(c for c in runner.shell_commands if "ln -sf" in c and "/etc/localtime" in c)
	# Relative path must start with ../usr/share/zoneinfo/, not /usr/share/zoneinfo/
	assert "../usr/share/zoneinfo/America/New_York" in ln_cmd, ln_cmd
	assert "/usr/share/zoneinfo/America" not in ln_cmd.replace("../usr", ""), ln_cmd
	print("PASS  symlink target uses relative path (../usr/share/zoneinfo/...)")


def test_timezone_before_locale_gen():
	runner = _FakeRunner()
	_setup_portage(GentlyConfig(system=SystemConfig(timezone="UTC", locale="en_US.UTF-8")), runner)
	timezone_idx = next(i for i, c in enumerate(runner.shell_commands) if "ln -sf" in c and "/etc/localtime" in c)
	locale_gen_idx = next(i for i, c in enumerate(runner.shell_commands) if c == "locale-gen")
	assert timezone_idx < locale_gen_idx, (
		f"timezone (idx={timezone_idx}) must come before locale-gen (idx={locale_gen_idx})"
	)
	print("PASS  timezone is configured before locale-gen")


def test_no_timezone_skips_timezone_setup():
	runner = _FakeRunner()
	_setup_portage(GentlyConfig(), runner)
	timezone_cmds = [c for c in runner.shell_commands if "/etc/localtime" in c or "timezone-data" in c]
	assert not timezone_cmds, f"Unexpected timezone commands when timezone is not set: {timezone_cmds}"
	print("PASS  missing timezone → no timezone commands issued")


# ---------------------------------------------------------------------------
# CPU flags tests
# ---------------------------------------------------------------------------

def test_cpu_flags_from_config_writes_package_use():
	cfg = GentlyConfig(portage=PortageConfig(cpu_flags=["mmx", "sse", "sse2"]))
	runner = _FakeRunner()
	_setup_cpu_flags(cfg, runner)
	cpu_cmds = [c for c in runner.shell_commands if "00cpu-flags" in c]
	assert len(cpu_cmds) == 1, f"Expected one write to 00cpu-flags: {runner.shell_commands}"
	assert "mmx" in cpu_cmds[0] and "sse2" in cpu_cmds[0], cpu_cmds[0]
	print("PASS  cpu_flags from config written to package.use/00cpu-flags")


def test_cpu_flags_from_config_no_emerge_cpuid2cpuflags():
	cfg = GentlyConfig(portage=PortageConfig(cpu_flags=["avx"]))
	runner = _FakeRunner()
	_setup_cpu_flags(cfg, runner)
	auto_cmds = [c for c in runner.shell_commands if "cpuid2cpuflags" in c]
	assert not auto_cmds, f"cpuid2cpuflags should not run when flags are explicit: {auto_cmds}"
	print("PASS  explicit cpu_flags skips cpuid2cpuflags emerge")


def test_cpu_flags_autodetect_emerges_cpuid2cpuflags():
	runner = _FakeRunner()
	_setup_cpu_flags(GentlyConfig(), runner)
	emerge_cmd = [c for c in runner.shell_commands if "app-portage/cpuid2cpuflags" in c]
	assert len(emerge_cmd) == 1, f"Expected emerge cpuid2cpuflags: {runner.shell_commands}"
	print("PASS  auto-detect emerges app-portage/cpuid2cpuflags")


def test_cpu_flags_autodetect_writes_shell_substitution():
	runner = _FakeRunner()
	_setup_cpu_flags(GentlyConfig(), runner)
	write_cmd = [c for c in runner.shell_commands if "00cpu-flags" in c]
	assert len(write_cmd) == 1, f"Expected write to 00cpu-flags: {runner.shell_commands}"
	assert "$(cpuid2cpuflags)" in write_cmd[0], write_cmd[0]
	print("PASS  auto-detect uses shell substitution $(cpuid2cpuflags)")


def test_cpu_flags_creates_package_use_dir():
	runner = _FakeRunner()
	_setup_cpu_flags(GentlyConfig(), runner)
	mkdir_cmds = [c for c in runner.shell_commands if "mkdir" in c and "package.use" in c]
	assert mkdir_cmds, f"Expected mkdir -p /etc/portage/package.use: {runner.shell_commands}"
	print("PASS  package.use directory created")


# ---------------------------------------------------------------------------
# Mirror tests
# ---------------------------------------------------------------------------

def test_mirrors_written_to_makeconf():
	cfg = GentlyConfig(portage=PortageConfig(
		cflags="-O2", makeopts="-j4",
		mirrors=["https://mirror.leaseweb.com/gentoo/", "https://mirrors.ircam.fr/pub/gentoo/"]
	))
	runner = _FakeRunner()
	_write_makeconf(cfg, runner)
	mirror_cmds = [c for c in runner.shell_commands if "GENTOO_MIRRORS" in c]
	assert len(mirror_cmds) == 1, f"Expected one GENTOO_MIRRORS line: {runner.shell_commands}"
	assert "mirror.leaseweb.com" in mirror_cmds[0], mirror_cmds[0]
	assert "mirrors.ircam.fr" in mirror_cmds[0], mirror_cmds[0]
	print("PASS  GENTOO_MIRRORS written to make.conf when mirrors are configured")


def test_no_mirrors_no_gentoo_mirrors_line():
	cfg = GentlyConfig(portage=PortageConfig(cflags="-O2", makeopts="-j4"))
	runner = _FakeRunner()
	_write_makeconf(cfg, runner)
	mirror_cmds = [c for c in runner.shell_commands if "GENTOO_MIRRORS" in c]
	assert not mirror_cmds, f"GENTOO_MIRRORS should not appear when mirrors are not configured: {mirror_cmds}"
	print("PASS  no mirrors configured → GENTOO_MIRRORS omitted from make.conf")


# ---------------------------------------------------------------------------
# Binpkg tests
# ---------------------------------------------------------------------------

def test_no_portage_binhost_in_makeconf():
	"""PORTAGE_BINHOST must never appear in make.conf (pre-3.x API — use binrepos.conf)."""
	cfg = GentlyConfig(portage=PortageConfig(binrepos=[PortageBinrepoConfig(name="gentoo", sync_uri="https://example.org/")]))
	runner = _FakeRunner()
	_write_makeconf(cfg, runner)
	assert not any("PORTAGE_BINHOST" in c for c in runner.shell_commands), runner.shell_commands
	print("PASS  PORTAGE_BINHOST never written to make.conf")


def test_binpkg_format_written_to_makeconf():
	cfg = GentlyConfig(portage=PortageConfig(binpkg_format="gpkg"))
	runner = _FakeRunner()
	_write_makeconf(cfg, runner)
	fmt_cmds = [c for c in runner.shell_commands if "BINPKG_FORMAT" in c]
	assert len(fmt_cmds) == 1, f"Expected BINPKG_FORMAT: {runner.shell_commands}"
	assert "gpkg" in fmt_cmds[0], fmt_cmds[0]
	print("PASS  BINPKG_FORMAT written to make.conf when binpkg_format is set")


def test_getbinpkg_adds_feature():
	cfg = GentlyConfig(portage=PortageConfig(getbinpkg=True))
	runner = _FakeRunner()
	_write_makeconf(cfg, runner)
	features_cmds = [c for c in runner.shell_commands if "FEATURES=" in c]
	assert any("getbinpkg" in c for c in features_cmds), f"FEATURES must include getbinpkg: {features_cmds}"
	print("PASS  getbinpkg=true → getbinpkg added to FEATURES")


def test_getbinpkg_merges_with_existing_features():
	cfg = GentlyConfig(portage=PortageConfig(features=["parallel-fetch"], getbinpkg=True))
	runner = _FakeRunner()
	_write_makeconf(cfg, runner)
	features_cmds = [c for c in runner.shell_commands if "FEATURES=" in c]
	assert len(features_cmds) == 1, f"Expected exactly one FEATURES line: {features_cmds}"
	assert "parallel-fetch" in features_cmds[0], features_cmds[0]
	assert "getbinpkg" in features_cmds[0], features_cmds[0]
	print("PASS  getbinpkg merges with existing FEATURES without duplicating the line")


def test_no_binpkg_config_no_binhost_line():
	cfg = GentlyConfig(portage=PortageConfig(cflags="-O2"))
	runner = _FakeRunner()
	_write_makeconf(cfg, runner)
	bad_cmds = [c for c in runner.shell_commands if "PORTAGE_BINHOST" in c or "BINPKG_FORMAT" in c]
	assert not bad_cmds, f"No binpkg config → no binhost lines expected: {bad_cmds}"
	print("PASS  no binpkg config → PORTAGE_BINHOST and BINPKG_FORMAT omitted")


# ---------------------------------------------------------------------------
# Binrepos tests
# ---------------------------------------------------------------------------

def test_binrepos_conf_created_per_repo():
	"""Each [[portage.binrepos]] entry creates its own <name>.conf file."""
	from installer.portage import _setup_binrepos
	cfg = GentlyConfig(portage=PortageConfig(binrepos=[
		PortageBinrepoConfig(name="gentoo", sync_uri="https://distfiles.gentoo.org/releases/amd64/binpackages/23.0/x86-64/"),
		PortageBinrepoConfig(name="gentoo-v3", sync_uri="https://distfiles.gentoo.org/releases/amd64/binpackages/23.0/x86-64-v3/"),
	]))
	runner = _FakeRunner()
	_setup_binrepos(cfg, runner)
	cmds = runner.shell_commands
	assert any("gentoo.conf" in c for c in cmds), f"gentoo.conf not created: {cmds}"
	assert any("gentoo-v3.conf" in c for c in cmds), f"gentoo-v3.conf not created: {cmds}"
	print("PASS  each binrepos entry creates its own .conf file")


def test_binrepos_conf_sync_uri_written():
	from installer.portage import _setup_binrepos
	uri = "https://distfiles.gentoo.org/releases/amd64/binpackages/23.0/x86-64/"
	cfg = GentlyConfig(portage=PortageConfig(binrepos=[
		PortageBinrepoConfig(name="gentoo", sync_uri=uri),
	]))
	runner = _FakeRunner()
	_setup_binrepos(cfg, runner)
	sync_cmds = [c for c in runner.shell_commands if "sync-uri" in c]
	assert len(sync_cmds) == 1, f"Expected one sync-uri line: {runner.shell_commands}"
	assert uri in sync_cmds[0], sync_cmds[0]
	print("PASS  sync-uri written correctly to binrepos.conf")


def test_binrepos_conf_verify_signature_default_true():
	from installer.portage import _setup_binrepos
	cfg = GentlyConfig(portage=PortageConfig(binrepos=[
		PortageBinrepoConfig(name="gentoo", sync_uri="https://example.org/"),
	]))
	runner = _FakeRunner()
	_setup_binrepos(cfg, runner)
	sig_cmds = [c for c in runner.shell_commands if "verify-signature" in c]
	assert any("true" in c for c in sig_cmds), f"verify-signature should default to true: {sig_cmds}"
	print("PASS  verify-signature defaults to true when not specified")


def test_binrepos_conf_custom_priority():
	from installer.portage import _setup_binrepos
	cfg = GentlyConfig(portage=PortageConfig(binrepos=[
		PortageBinrepoConfig(name="gentoo", sync_uri="https://example.org/", priority=5000),
	]))
	runner = _FakeRunner()
	_setup_binrepos(cfg, runner)
	prio_cmds = [c for c in runner.shell_commands if "priority" in c]
	assert any("5000" in c for c in prio_cmds), f"Expected priority 5000: {prio_cmds}"
	print("PASS  custom priority written to binrepos.conf")


def test_binrepos_conf_calls_getuto_when_verify_signature():
	from installer.portage import _setup_binrepos
	cfg = GentlyConfig(portage=PortageConfig(binrepos=[
		PortageBinrepoConfig(name="gentoo", sync_uri="https://example.org/", verify_signature=True),
	]))
	runner = _FakeRunner()
	_setup_binrepos(cfg, runner)
	assert any("getuto" in c for c in runner.shell_commands), f"getuto not called: {runner.shell_commands}"
	print("PASS  getuto called when at least one repo has verify_signature=true")


def test_binrepos_conf_no_getuto_when_verify_disabled():
	from installer.portage import _setup_binrepos
	cfg = GentlyConfig(portage=PortageConfig(binrepos=[
		PortageBinrepoConfig(name="gentoo", sync_uri="https://example.org/", verify_signature=False),
	]))
	runner = _FakeRunner()
	_setup_binrepos(cfg, runner)
	assert not any("getuto" in c for c in runner.shell_commands), f"getuto should not run: {runner.shell_commands}"
	print("PASS  getuto skipped when all repos have verify_signature=false")


def test_binrepos_conf_skipped_when_no_binrepos():
	from installer.portage import _setup_binrepos
	runner = _FakeRunner()
	_setup_binrepos(GentlyConfig(), runner)
	assert not runner.shell_commands, f"No binrepos → no commands expected: {runner.shell_commands}"
	print("PASS  _setup_binrepos no-ops when portage.binrepos is absent")


def test_binrepos_sync_uri_inferred_from_mirror():
	"""With no sync_uri, URL is built from stage3.mirror + arch + profile version + subarch."""
	from installer.portage import _setup_binrepos
	cfg = GentlyConfig(
		stage3=Stage3Config(mirror="https://distfiles.gentoo.org", arch="amd64"),
		portage=PortageConfig(
			profile=PortageProfileConfig(name="default/linux/amd64/23.0/desktop"),
			binrepos=[PortageBinrepoConfig(name="gentoo", subarch="x86-64")],
		),
	)
	runner = _FakeRunner()
	_setup_binrepos(cfg, runner)
	sync_cmds = [c for c in runner.shell_commands if "sync-uri" in c]
	assert len(sync_cmds) == 1, f"Expected one sync-uri: {runner.shell_commands}"
	expected = "https://distfiles.gentoo.org/releases/amd64/binpackages/23.0/x86-64/"
	assert expected in sync_cmds[0], f"Expected {expected!r} in {sync_cmds[0]!r}"
	print("PASS  sync-uri inferred from mirror + arch + profile version + subarch")


def test_binrepos_sync_uri_inferred_default_subarch():
	"""When subarch is absent, defaults to x86-64."""
	from installer.portage import _setup_binrepos
	cfg = GentlyConfig(
		stage3=Stage3Config(mirror="https://distfiles.gentoo.org", arch="amd64"),
		portage=PortageConfig(
			profile=PortageProfileConfig(name="default/linux/amd64/23.0/desktop"),
			binrepos=[PortageBinrepoConfig(name="gentoo")],
		),
	)
	runner = _FakeRunner()
	_setup_binrepos(cfg, runner)
	sync_cmds = [c for c in runner.shell_commands if "sync-uri" in c]
	assert any("x86-64" in c for c in sync_cmds), f"Expected x86-64 default: {sync_cmds}"
	print("PASS  subarch defaults to x86-64 when not specified")


def test_binrepos_sync_uri_explicit_takes_precedence():
	"""Explicit sync_uri wins over the inferred one."""
	from installer.portage import _setup_binrepos
	explicit_uri = "https://custom.mirror.org/binpackages/23.0/x86-64/"
	cfg = GentlyConfig(
		stage3=Stage3Config(mirror="https://distfiles.gentoo.org", arch="amd64"),
		portage=PortageConfig(
			profile=PortageProfileConfig(name="default/linux/amd64/23.0/desktop"),
			binrepos=[PortageBinrepoConfig(name="gentoo", sync_uri=explicit_uri, subarch="x86-64")],
		),
	)
	runner = _FakeRunner()
	_setup_binrepos(cfg, runner)
	sync_cmds = [c for c in runner.shell_commands if "sync-uri" in c]
	assert any(explicit_uri in c for c in sync_cmds), f"Expected explicit URI: {sync_cmds}"
	assert not any("distfiles.gentoo.org" in c for c in sync_cmds), f"Inferred URI leaked: {sync_cmds}"
	print("PASS  explicit sync_uri takes precedence over inferred URL")


def test_binrepos_sync_uri_extracts_profile_version():
	"""Profile version is extracted correctly from various profile name formats."""
	from installer.portage import _binrepos_sync_uri
	cfg = GentlyConfig(
		stage3=Stage3Config(mirror="https://distfiles.gentoo.org", arch="amd64"),
		portage=PortageConfig(profile=PortageProfileConfig(name="default/linux/amd64/17.1/desktop/plasma")),
	)
	from model.config import PortageBinrepoConfig as _BR
	uri = _binrepos_sync_uri(cfg, _BR(name="gentoo"))
	assert "/17.1/" in uri, f"Expected profile version 17.1 in URI: {uri}"
	print("PASS  profile version extracted correctly from profile name")


# ---------------------------------------------------------------------------
# Distcc tests
# ---------------------------------------------------------------------------

def test_distcc_disabled_no_commands():
	runner = _FakeRunner()
	_setup_distcc(_cfg_distcc(enabled=False, hosts=["192.168.1.10/4"]), runner)
	assert not runner.shell_commands
	assert not runner.host_commands
	print("PASS  distcc disabled → no commands issued")


def test_distcc_no_config_no_commands():
	runner = _FakeRunner()
	_setup_distcc(GentlyConfig(), runner)
	assert not runner.shell_commands
	assert not runner.host_commands
	print("PASS  distcc=None → no commands issued")


def test_distcc_no_hosts_raises():
	from installer.portage import PortageError as PError
	runner = _FakeRunner()
	try:
		_setup_distcc(_cfg_distcc(enabled=True, hosts=None), runner)
	except PError as exc:
		assert "empty" in str(exc), f"Expected 'empty' in: {exc}"
		print("PASS  distcc enabled but no hosts → PortageError raised")
		return
	raise AssertionError("Expected PortageError for empty hosts")


def test_distcc_writes_hosts_file_inside_chroot():
	"""The hosts file must be written via run_shell (inside the chroot)."""
	runner = _FakeRunner()
	_setup_distcc(_cfg_distcc(enabled=True, hosts=["192.168.1.10/4", "192.168.1.11/4"]), runner)
	assert any("distcc-config --set-hosts" in c for c in runner.shell_commands), \
		f"Expected distcc-config --set-hosts in chroot commands: {runner.shell_commands}"
	# Must NOT appear in host commands
	assert not any("distcc-config --set-hosts" in c for c in runner.host_commands), \
		"hosts configuration must run inside chroot, not on the host"
	print("PASS  distcc-config --set-hosts runs inside chroot")


def test_distcc_hosts_string_in_hosts_file():
	runner = _FakeRunner()
	_setup_distcc(_cfg_distcc(enabled=True, hosts=["h1/4", "h2/4"]), runner)
	hosts_cmd = next(c for c in runner.shell_commands if "distcc-config --set-hosts" in c)
	assert "h1/4" in hosts_cmd and "h2/4" in hosts_cmd, hosts_cmd
	print("PASS  all configured hosts appear in the distcc-config command")


def test_distcc_pump_mode_adds_prefix():
	runner = _FakeRunner()
	_setup_distcc(_cfg_distcc(enabled=True, hosts=["192.168.1.10/4"], pump_mode=True), runner)
	hosts_cmd = next(c for c in runner.shell_commands if "distcc-config --set-hosts" in c)
	assert "++192.168.1.10/4" in hosts_cmd, f"Expected ++ prefix in: {hosts_cmd}"
	print("PASS  pump mode adds ++ prefix to each host")


def test_distcc_no_pump_mode_no_prefix():
	runner = _FakeRunner()
	_setup_distcc(_cfg_distcc(enabled=True, hosts=["192.168.1.10/4"], pump_mode=False), runner)
	hosts_cmd = next(c for c in runner.shell_commands if "distcc-config --set-hosts" in c)
	assert "++" not in hosts_cmd, f"Unexpected ++ prefix in: {hosts_cmd}"
	print("PASS  regular mode has no ++ prefix in hosts")


def test_distcc_installed_inside_chroot():
	"""distcc must be emerged inside the chroot via run_shell with chroot=True."""
	runner = _FakeRunner()
	_setup_distcc(_cfg_distcc(enabled=True, hosts=["h1/4"]), runner)
	emerge_cmds = [c for c in runner.shell_commands if "sys-devel/distcc" in c]
	assert len(emerge_cmds) >= 1, f"Expected emerge sys-devel/distcc in chroot: {runner.shell_commands}"
	print("PASS  sys-devel/distcc is emerged inside the chroot")


def test_distcc_tcp_check_runs_as_warning():
	"""TCP connectivity check runs as a non-blocking warning."""
	runner = _FakeRunner()
	_setup_distcc(_cfg_distcc(enabled=True, hosts=["192.168.1.10/4"]), runner)
	tcp_cmds = [c for c in runner.host_commands if "socket.create_connection" in c]
	assert len(tcp_cmds) >= 1, f"Expected TCP check command: {runner.host_commands}"
	print("PASS  TCP connectivity check issued (non-blocking)")


def test_execute_skips_distcc_when_disabled():
	runner = _FakeRunner()
	execute(GentlyConfig(), runner)
	# execute() runs portage setup (locale-gen, emerge-webrsync, make.conf etc.)
	# These are normal portage operations, not distcc-related.
	distcc_cmds = [c for c in runner.shell_commands if "distcc" in c.lower()]
	assert not distcc_cmds, \
		f"distcc-related commands should not appear when distcc is disabled: {distcc_cmds}"
	print("PASS  execute() with no distcc config runs portage setup normally")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

if __name__ == "__main__":
	test_profile_no_config_skips_eselect()
	test_profile_explicit_valid_applies()
	test_profile_explicit_invalid_raises()
	test_profile_validation_before_set()
	test_profile_set_runs_after_webrsync()
	test_timezone_creates_symlink()
	test_timezone_symlink_uses_relative_path()
	test_timezone_before_locale_gen()
	test_no_timezone_skips_timezone_setup()
	test_cpu_flags_from_config_writes_package_use()
	test_cpu_flags_from_config_no_emerge_cpuid2cpuflags()
	test_cpu_flags_autodetect_emerges_cpuid2cpuflags()
	test_cpu_flags_autodetect_writes_shell_substitution()
	test_cpu_flags_creates_package_use_dir()
	test_mirrors_written_to_makeconf()
	test_no_mirrors_no_gentoo_mirrors_line()
	test_no_portage_binhost_in_makeconf()
	test_binpkg_format_written_to_makeconf()
	test_getbinpkg_adds_feature()
	test_getbinpkg_merges_with_existing_features()
	test_no_binpkg_config_no_binhost_line()
	test_binrepos_conf_created_per_repo()
	test_binrepos_conf_sync_uri_written()
	test_binrepos_conf_verify_signature_default_true()
	test_binrepos_conf_custom_priority()
	test_binrepos_conf_calls_getuto_when_verify_signature()
	test_binrepos_conf_no_getuto_when_verify_disabled()
	test_binrepos_conf_skipped_when_no_binrepos()
	test_binrepos_sync_uri_inferred_from_mirror()
	test_binrepos_sync_uri_inferred_default_subarch()
	test_binrepos_sync_uri_explicit_takes_precedence()
	test_binrepos_sync_uri_extracts_profile_version()
	test_distcc_disabled_no_commands()
	test_distcc_no_config_no_commands()
	test_distcc_no_hosts_raises()
	test_distcc_writes_hosts_file_inside_chroot()
	test_distcc_hosts_string_in_hosts_file()
	test_distcc_pump_mode_adds_prefix()
	test_distcc_no_pump_mode_no_prefix()
	test_distcc_installed_inside_chroot()
	test_distcc_tcp_check_runs_as_warning()
	test_execute_skips_distcc_when_disabled()
	print("\nAll tests passed.")
