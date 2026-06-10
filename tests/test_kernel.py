"""
Contract tests for installer.kernel.

Run with:
    python3 tests/test_kernel.py
"""
from __future__ import annotations

import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "vendor"))

from installer.kernel import KernelError, execute
from installer.runner import CommandResult
from model.config import GentlyConfig, KernelConfig


# ---------------------------------------------------------------------------
# Fake runner
# ---------------------------------------------------------------------------

class _FakeRunner:
    transport = "local"

    def __init__(self, *, dry_run: bool = False):
        self.dry_run = dry_run
        self.shell_commands: list[tuple[str, bool]] = []  # (command, chroot)
        self.chroot_path: str | None = "/mnt/gentoo"
        self.log_dispatcher = None
        self.confirm_callback = None

    def run_shell(
        self,
        command: str,
        check: bool = True,
        cwd=None,
        env=None,
        phase=None,
        chroot: bool = False,
    ) -> CommandResult:
        self.shell_commands.append((command, chroot))
        return CommandResult(
            argv=["bash", "-lc", command],
            returncode=0,
            stdout="",
            stderr="",
            duration_sec=0.0,
            transport=self.transport,
            phase=phase,
        )


def _cfg(method=None, binary=None, linux_firmware=None, intel_microcode=None, sof_firmware=None) -> GentlyConfig:
    return GentlyConfig(kernel=KernelConfig(
        method=method, binary=binary,
        linux_firmware=linux_firmware,
        intel_microcode=intel_microcode,
        sof_firmware=sof_firmware,
    ))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_installkernel_binary_true_emerges_bin():
    runner = _FakeRunner()
    execute(_cfg("installkernel", binary=True), runner)
    # Should have at least 3 commands: mkdir, echo, emerge
    assert len(runner.shell_commands) >= 3
    # First command: create package.use directory
    assert any("mkdir -p" in cmd for cmd, _ in runner.shell_commands)
    # Second command: write package.use file
    assert any("sys-kernel/installkernel dracut" in cmd for cmd, _ in runner.shell_commands)
    # Last command: emerge gentoo-kernel-bin
    cmd, _ = runner.shell_commands[-1]
    assert "sys-kernel/gentoo-kernel-bin" in cmd
    print("PASS  installkernel binary=True emerges sys-kernel/gentoo-kernel-bin")


def test_installkernel_binary_defaults_to_true():
    runner = _FakeRunner()
    execute(_cfg("installkernel"), runner)
    # Should have at least 3 commands: mkdir, echo, emerge
    assert len(runner.shell_commands) >= 3
    # First command: create package.use directory
    assert any("mkdir -p" in cmd for cmd, _ in runner.shell_commands)
    # Second command: write package.use file
    assert any("sys-kernel/installkernel dracut" in cmd for cmd, _ in runner.shell_commands)
    # Last command: emerge gentoo-kernel-bin
    cmd, _ = runner.shell_commands[-1]
    assert "sys-kernel/gentoo-kernel-bin" in cmd
    print("PASS  installkernel binary=None defaults to gentoo-kernel-bin")


def test_installkernel_binary_false_emerges_source():
    runner = _FakeRunner()
    execute(_cfg("installkernel", binary=False), runner)
    # Should have at least 3 commands: mkdir, echo, emerge
    assert len(runner.shell_commands) >= 3
    # First command: create package.use directory
    assert any("mkdir -p" in cmd for cmd, _ in runner.shell_commands)
    # Second command: write package.use file
    assert any("sys-kernel/installkernel dracut" in cmd for cmd, _ in runner.shell_commands)
    # Last command: emerge gentoo-kernel (source)
    cmd, _ = runner.shell_commands[-1]
    assert "sys-kernel/gentoo-kernel-bin" not in cmd
    assert "sys-kernel/gentoo-kernel" in cmd
    print("PASS  installkernel binary=False emerges sys-kernel/gentoo-kernel (source)")


def test_installkernel_emerge_runs_in_chroot():
    runner = _FakeRunner()
    execute(_cfg("installkernel"), runner)
    # All commands should run in chroot
    for cmd, chroot in runner.shell_commands:
        assert chroot is True, f"Command '{cmd}' should run in chroot"
    print("PASS  installkernel emerge runs with chroot=True")


def test_reserved_methods_raise_not_implemented():
    for method in ("menuconfig", "custom"):
        runner = _FakeRunner()
        try:
            execute(_cfg(method), runner)
            assert False, f"Expected KernelError for method '{method}'"
        except KernelError as e:
            assert "not implemented in v1" in str(e), str(e)
            assert not runner.shell_commands
        print(f"PASS  method '{{method}}' raises KernelError with 'not implemented in v1'")


def test_unknown_method_raises():
    runner = _FakeRunner()
    try:
        execute(_cfg("something_weird"), runner)
        assert False, "Expected KernelError"
    except KernelError as e:
        assert "Unknown kernel method" in str(e), str(e)
        assert not runner.shell_commands
    print("PASS  unknown method raises KernelError with 'Unknown kernel method'")


def test_no_method_raises():
    for cfg in (_cfg(None), GentlyConfig()):
        runner = _FakeRunner()
        try:
            execute(cfg, runner)
            assert False, "Expected KernelError"
        except KernelError as e:
            assert "not set" in str(e), str(e)
            assert not runner.shell_commands
    print("PASS  missing kernel.method raises KernelError with 'not set'")


def test_dry_run_records_command():
    runner = _FakeRunner(dry_run=True)
    execute(_cfg("installkernel"), runner)
    assert len(runner.shell_commands) == 1
    cmd, chroot = runner.shell_commands[0]
    assert "gentoo-kernel" in cmd
    assert chroot is True
    print("PASS  dry_run mode records the emerge command without executing")


def test_no_firmware_flags_no_second_emerge():
    runner = _FakeRunner()
    execute(_cfg("installkernel"), runner)
    assert len(runner.shell_commands) == 1
    print("PASS  no firmware flags → only one emerge command")


def test_linux_firmware_emerges_package():
    runner = _FakeRunner()
    execute(_cfg("installkernel", linux_firmware=True), runner)
    assert len(runner.shell_commands) == 2
    cmd, _ = runner.shell_commands[1]
    assert "sys-kernel/linux-firmware" in cmd
    print("PASS  linux_firmware=True emerges sys-kernel/linux-firmware")


def test_intel_microcode_emerges_package():
    runner = _FakeRunner()
    execute(_cfg("installkernel", intel_microcode=True), runner)
    assert len(runner.shell_commands) == 2
    cmd, _ = runner.shell_commands[1]
    assert "sys-firmware/intel-microcode" in cmd
    print("PASS  intel_microcode=True emerges sys-firmware/intel-microcode")


def test_sof_firmware_emerges_package():
    runner = _FakeRunner()
    execute(_cfg("installkernel", sof_firmware=True), runner)
    assert len(runner.shell_commands) == 2
    cmd, _ = runner.shell_commands[1]
    assert "sys-firmware/sof-firmware" in cmd
    print("PASS  sof_firmware=True emerges sys-firmware/sof-firmware")


def test_multiple_firmware_in_single_emerge():
    runner = _FakeRunner()
    execute(_cfg("installkernel", linux_firmware=True, intel_microcode=True, sof_firmware=True), runner)
    assert len(runner.shell_commands) == 2
    cmd, chroot = runner.shell_commands[1]
    assert "sys-kernel/linux-firmware" in cmd
    assert "sys-firmware/intel-microcode" in cmd
    assert "sys-firmware/sof-firmware" in cmd
    assert chroot is True
    print("PASS  multiple firmware flags → single emerge with all packages")


if __name__ == "__main__":
    test_installkernel_binary_true_emerges_bin()
    test_installkernel_binary_defaults_to_true()
    test_installkernel_binary_false_emerges_source()
    test_installkernel_emerge_runs_in_chroot()
    test_reserved_methods_raise_not_implemented()
    test_unknown_method_raises()
    test_no_method_raises()
    test_dry_run_records_command()
    test_no_firmware_flags_no_second_emerge()
    test_linux_firmware_emerges_package()
    test_intel_microcode_emerges_package()
    test_sof_firmware_emerges_package()
    test_multiple_firmware_in_single_emerge()
    print("\nAll kernel tests passed.")
