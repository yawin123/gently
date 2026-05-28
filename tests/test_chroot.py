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

from installer.chroot import MOUNTPOINT, execute
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
        self.log_callback = None
        self.confirm_callback = None

    def run_shell(self, command: str, check: bool = True, cwd=None, env=None, phase=None) -> CommandResult:
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
    assert len(runner.cleanup_stack) == 4, f"Expected 4 cleanup entries, got {len(runner.cleanup_stack)}"
    descs = [desc for desc, _ in runner.cleanup_stack]
    assert any("/proc" in d for d in descs)
    assert any("/sys"  in d for d in descs)
    assert any("/dev"  in d for d in descs)
    assert any("/run"  in d for d in descs)
    print("PASS  execute registers 4 cleanup entries (one per virtual mount)")


def test_execute_cleanup_order_is_lifo():
    """Cleanup must unmount in reverse mount order: run → dev → sys → proc."""
    runner = _FakeRunner()
    execute(GentlyConfig(), runner)
    # pop() gives LIFO order
    popped = []
    while runner.cleanup_stack:
        desc, _ = runner.cleanup_stack.pop()
        popped.append(desc)
    assert "/run"  in popped[0], popped
    assert "/dev"  in popped[1], popped
    assert "/sys"  in popped[2], popped
    assert "/proc" in popped[3], popped
    print("PASS  cleanup order is LIFO: run → dev → sys → proc")


def test_execute_copies_resolv_conf():
    runner = _FakeRunner()
    execute(GentlyConfig(), runner)
    cp_cmds = [c for c in runner.shell_commands if c.startswith("cp ")]
    assert len(cp_cmds) == 1, f"Expected 1 cp command, got: {cp_cmds}"
    assert "resolv.conf" in cp_cmds[0]
    assert MOUNTPOINT in cp_cmds[0]
    print("PASS  execute copies resolv.conf into mountpoint")


def test_execute_sets_chroot_path():
    runner = _FakeRunner()
    execute(GentlyConfig(), runner)
    assert runner.chroot_path == MOUNTPOINT
    print("PASS  execute sets runner.chroot_path to mountpoint")


def test_execute_dry_run_sets_chroot_path_without_mounts():
    runner = _FakeRunner(dry_run=True)
    execute(GentlyConfig(), runner)
    assert runner.chroot_path == MOUNTPOINT, runner.chroot_path
    assert not runner.shell_commands, runner.shell_commands
    assert not runner.cleanup_stack
    print("PASS  dry-run sets chroot_path but skips all mounts")


def test_run_shell_wraps_command_when_chroot_path_set():
    """LocalRunner.run_shell must wrap commands with chroot when chroot_path is set."""
    runner = LocalRunner(dry_run=True)
    runner.chroot_path = "/mnt/gentoo"
    result = runner.run_shell("emerge --sync", phase="test")
    # In dry-run the command is skipped, but the argv should reflect the chroot wrap.
    assert result.argv == ["chroot", "/mnt/gentoo", "/bin/bash", "-lc", "emerge --sync"], result.argv
    print("PASS  run_shell wraps command with chroot when chroot_path is set")


def test_run_shell_no_wrap_when_chroot_path_unset():
    runner = LocalRunner(dry_run=True)
    result = runner.run_shell("ls /", phase="test")
    assert result.argv == ["bash", "-lc", "ls /"], result.argv
    print("PASS  run_shell does NOT wrap when chroot_path is None")


def test_run_cleanup_clears_chroot_path():
    runner = LocalRunner(dry_run=True)
    runner.chroot_path = "/mnt/gentoo"
    runner.run_cleanup()
    assert runner.chroot_path is None
    print("PASS  run_cleanup clears chroot_path before running cleanup actions")


if __name__ == "__main__":
    test_execute_mounts_proc_sys_dev_run()
    test_execute_mounts_in_correct_order()
    test_execute_registers_cleanup_for_each_mount()
    test_execute_cleanup_order_is_lifo()
    test_execute_copies_resolv_conf()
    test_execute_sets_chroot_path()
    test_execute_dry_run_sets_chroot_path_without_mounts()
    test_run_shell_wraps_command_when_chroot_path_set()
    test_run_shell_no_wrap_when_chroot_path_unset()
    test_run_cleanup_clears_chroot_path()
    print()
    print("All chroot tests passed.")
