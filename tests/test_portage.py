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

from installer.portage import MOUNTPOINT, _setup_distcc, execute
from installer.runner import CommandResult, CommandSpec
from model.config import DistccConfig, GentlyConfig


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

	def run_shell(self, command: str, check: bool = True, cwd=None, env=None, phase=None, chroot: bool = False):
		if chroot:
			self.shell_commands.append(command)
		else:
			self.host_commands.append(command)
		return self._result(["bash", "-lc", command], phase)

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
# Tests
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
