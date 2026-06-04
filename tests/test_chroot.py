"""
Contract tests for installer.chroot (chroot_prep phase).

Run with:
    python3 tests/test_chroot.py
"""

from __future__ import annotations

import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "vendor"))

from installer.chroot import MOUNTPOINT, ChrootError, _check_stage3_cpu_compat, execute
from installer.runner import CommandResult, CommandSpec, LocalRunner
from model.config import GentlyConfig


# ---------------------------------------------------------------------------
# Minimal fake runner (records shell commands; stubs cleanup stack)
# ---------------------------------------------------------------------------

class _FakeRunner:
    transport = "local"

    def __init__(self, *, dry_run: bool = False):
        self.dry_run = dry_run
        self.shell_commands: list[str] = []
        self.cleanup_stack: list[tuple[str, object]] = []
        self.chroot_path: str | None = None
        self.log_dispatcher = None
        self.confirm_callback = None

    def run_shell(self, command: str, check: bool = True, cwd=None, env=None, phase=None, chroot: bool = False) -> CommandResult:
        self.shell_commands.append(command)
        return CommandResult(
            argv=["bash", "-lc", command],
            returncode=0, stdout="", stderr="",
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
# Tests
# ---------------------------------------------------------------------------

def test_execute_mounts_proc_sys_dev_run():
    runner = _FakeRunner()
    execute(GentlyConfig(), runner)
    mount_cmds = [c for c in runner.shell_commands if c.startswith("mount ")]
    # Expect: proc, sys, make-rslave sys, dev, make-rslave dev, run = 6 mounts
    assert len(mount_cmds) == 6, f"Expected 6 mount commands, got: {mount_cmds}"
    targets = " ".join(mount_cmds)
    assert "/proc" in targets
    assert "/sys"  in targets
    assert "/dev"  in targets
    assert "/run"  in targets
    assert targets.count("make-rslave") == 2  # sys and dev
    print("PASS  execute mounts proc, sys (rslave), dev (rslave), run")


def test_execute_mounts_in_correct_order():
    runner = _FakeRunner()
    execute(GentlyConfig(), runner)
    mount_cmds = [c for c in runner.shell_commands if c.startswith("mount ") and "make-rslave" not in c]
    # Order must be: proc → sys → dev → run
    order = [c for c in mount_cmds]
    assert "/proc" in order[0], order
    assert "/sys"  in order[1], order
    assert "/dev"  in order[2], order
    assert "/run"  in order[3], order
    print("PASS  execute mounts in order: proc, sys, dev, run")


def test_execute_registers_cleanup_for_each_mount():
    runner = _FakeRunner()
    execute(GentlyConfig(), runner)
    # 4 virtual mount umounts + 1 chroot deactivation = 5 entries
    assert len(runner.cleanup_stack) == 5, f"Expected 5 cleanup entries, got {len(runner.cleanup_stack)}"
    descs = [desc for desc, _ in runner.cleanup_stack]
    assert any("/proc" in d for d in descs)
    assert any("/sys"  in d for d in descs)
    assert any("/dev"  in d for d in descs)
    assert any("/run"  in d for d in descs)
    assert any("deactivate chroot" in d for d in descs)
    print("PASS  execute registers 5 cleanup entries (4 mounts + deactivate chroot)")


def test_execute_cleanup_order_is_lifo():
    """Cleanup LIFO order: deactivate chroot first, then unmount run → dev → sys → proc."""
    runner = _FakeRunner()
    execute(GentlyConfig(), runner)
    # pop() gives LIFO order: last-pushed first
    popped = []
    while runner.cleanup_stack:
        desc, _ = runner.cleanup_stack.pop()
        popped.append(desc)
    # "deactivate chroot" is pushed last so it runs first (clears chroot_path
    # before the umounts, which must run on the host)
    assert "deactivate chroot" in popped[0], popped
    assert "/run"  in popped[1], popped
    assert "/dev"  in popped[2], popped
    assert "/sys"  in popped[3], popped
    assert "/proc" in popped[4], popped
    print("PASS  cleanup order is LIFO: deactivate chroot → run → dev → sys → proc")


def test_execute_copies_resolv_conf():
    runner = _FakeRunner()
    execute(GentlyConfig(), runner)
    cp_cmds = [c for c in runner.shell_commands if c.startswith("cp ")]
    assert len(cp_cmds) >= 2, f"Expected at least 2 cp commands (localtime + resolv.conf), got: {cp_cmds}"
    resolv_cmds = [c for c in cp_cmds if "resolv.conf" in c]
    localtime_cmds = [c for c in cp_cmds if "localtime" in c]
    assert len(resolv_cmds) == 1, f"Expected 1 resolv.conf cp, got: {resolv_cmds}"
    assert len(localtime_cmds) == 1, f"Expected 1 localtime cp, got: {localtime_cmds}"
    assert MOUNTPOINT in resolv_cmds[0]
    print("PASS  execute copies localtime and resolv.conf into mountpoint")


def test_execute_sets_chroot_path():
    runner = _FakeRunner()
    execute(GentlyConfig(), runner)
    assert runner.chroot_path == MOUNTPOINT
    print("PASS  execute sets runner.chroot_path to mountpoint")


def test_execute_dry_run_still_issues_commands():
    """In dry_run the runner handles no-op execution; execute() must not skip commands."""
    runner = _FakeRunner(dry_run=True)
    execute(GentlyConfig(), runner)
    assert runner.chroot_path == MOUNTPOINT, runner.chroot_path
    # Commands are issued (runner logs them as [dry-run] and skips actual execution).
    mount_cmds = [c for c in runner.shell_commands if c.startswith("mount ")]
    assert len(mount_cmds) == 6, f"Expected 6 mount commands in dry-run, got: {mount_cmds}"
    assert len(runner.cleanup_stack) == 5
    print("PASS  dry-run still issues commands and registers cleanups (runner handles the no-op)")


def test_run_shell_wraps_command_when_chroot_path_set():
    """LocalRunner.run_shell must wrap commands with chroot when chroot=True and chroot_path is set."""
    runner = LocalRunner(dry_run=True)
    runner.chroot_path = "/mnt/gentoo"
    result = runner.run_shell("emerge --sync", phase="test", chroot=True)
    assert result.argv == ["chroot", "/mnt/gentoo", "/bin/bash", "-lc", "emerge --sync"], result.argv
    print("PASS  run_shell wraps command with chroot when chroot_path is set")


def test_run_shell_no_wrap_when_chroot_false():
    """run_shell(chroot=False) must never wrap, even if chroot_path is set."""
    runner = LocalRunner(dry_run=True)
    runner.chroot_path = "/mnt/gentoo"
    result = runner.run_shell("mount --bind /dev /mnt/gentoo/dev", phase="test")
    assert result.argv == ["bash", "-lc", "mount --bind /dev /mnt/gentoo/dev"], result.argv
    print("PASS  run_shell does NOT wrap when chroot=False (default)")


def test_run_cleanup_clears_chroot_path():
    """chroot_path is cleared via the registered cleanup entry, not by run_cleanup itself."""
    runner = _FakeRunner()
    execute(GentlyConfig(), runner)
    assert runner.chroot_path == MOUNTPOINT
    # Manually invoke the "deactivate chroot" cleanup entry (it is the last pushed).
    # In production run_cleanup() drains the stack in LIFO order, so it fires first.
    deactivate_entry = next(
        (action for desc, action in reversed(runner.cleanup_stack) if "deactivate chroot" in desc),
        None,
    )
    assert deactivate_entry is not None, "Expected 'deactivate chroot' cleanup entry"
    deactivate_entry()
    assert runner.chroot_path is None
    print("PASS  'deactivate chroot' cleanup entry clears runner.chroot_path")


# ---------------------------------------------------------------------------
# Tests for _check_stage3_cpu_compat
# ---------------------------------------------------------------------------

class _SedRunner(_FakeRunner):
    """Fake runner that simulates SIGILL from /usr/bin/sed."""

    def __init__(self, *, sed_returncode: int = 0, sed_stderr: str = ""):
        super().__init__()
        self._sed_returncode = sed_returncode
        self._sed_stderr = sed_stderr

    def run_shell(self, command: str, check: bool = True, cwd=None, env=None, phase=None, chroot: bool = False) -> CommandResult:
        rc = self._sed_returncode if "/usr/bin/sed" in command else 0
        stderr = self._sed_stderr if "/usr/bin/sed" in command else ""
        return CommandResult(
            argv=["bash", "-lc", command],
            returncode=rc, stdout="", stderr=stderr,
            duration_sec=0.0, transport=self.transport, phase=phase,
        )


def test_cpu_compat_passes_when_sed_succeeds():
    runner = _SedRunner(sed_returncode=0)
    runner.chroot_path = MOUNTPOINT
    # Should not raise
    _check_stage3_cpu_compat(runner)
    print("PASS  _check_stage3_cpu_compat does not raise when sed exits 0")


def test_cpu_compat_raises_on_sigill_exit_code():
    runner = _SedRunner(sed_returncode=132)  # 128 + SIGILL(4)
    runner.chroot_path = MOUNTPOINT
    try:
        _check_stage3_cpu_compat(runner)
        assert False, "Expected ChrootError"
    except ChrootError as exc:
        assert "Illegal instruction" in str(exc)
        assert "stage3" in str(exc).lower()
    print("PASS  _check_stage3_cpu_compat raises ChrootError on exit code 132 (SIGILL)")


def test_cpu_compat_raises_on_sigill_in_stderr():
    runner = _SedRunner(sed_returncode=1, sed_stderr="Illegal instruction")
    runner.chroot_path = MOUNTPOINT
    try:
        _check_stage3_cpu_compat(runner)
        assert False, "Expected ChrootError"
    except ChrootError as exc:
        assert "Illegal instruction" in str(exc)
    print("PASS  _check_stage3_cpu_compat raises ChrootError when stderr contains 'Illegal instruction'")


if __name__ == "__main__":
    test_execute_mounts_proc_sys_dev_run()
    test_execute_mounts_in_correct_order()
    test_execute_registers_cleanup_for_each_mount()
    test_execute_cleanup_order_is_lifo()
    test_execute_copies_resolv_conf()
    test_execute_sets_chroot_path()
    test_execute_dry_run_still_issues_commands()
    test_run_shell_wraps_command_when_chroot_path_set()
    test_run_shell_no_wrap_when_chroot_false()
    test_run_cleanup_clears_chroot_path()
    test_cpu_compat_passes_when_sed_succeeds()
    test_cpu_compat_raises_on_sigill_exit_code()
    test_cpu_compat_raises_on_sigill_in_stderr()
    print()
    print("All chroot tests passed.")
