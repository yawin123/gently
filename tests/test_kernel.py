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
        
        # Simulate blkid response for root device (always, even in dry_run mode)
        if "blkid -s UUID -o value" in command:
            return CommandResult(
                argv=["bash", "-lc", command],
                returncode=0,
                stdout="12345678-1234-1234-1234-123456789012",
                stderr="",
                duration_sec=0.0,
                transport=self.transport,
                phase=phase,
            )
        
        return CommandResult(
            argv=["bash", "-lc", command],
            returncode=0,
            stdout="",
            stderr="",
            duration_sec=0.0,
            transport=self.transport,
            phase=phase,
        )


def _cfg(method=None, binary=None, linux_firmware=None, intel_microcode=None,
         sof_firmware=None, bootloader_type=None,
         modules_sign=None, secureboot=None, sign_key=None, sign_cert=None,
         sign_hash=None) -> GentlyConfig:
    custom = None
    if sign_key or sign_cert or sign_hash:
        from model.config import KernelCustomConfig
        custom = KernelCustomConfig(
            sign_key=sign_key, sign_cert=sign_cert, sign_hash=sign_hash
        )
    kernel = KernelConfig(
        method=method, binary=binary,
        linux_firmware=linux_firmware,
        intel_microcode=intel_microcode,
        sof_firmware=sof_firmware,
        modules_sign=modules_sign,
        secureboot=secureboot,
        custom=custom,
    )
    return GentlyConfig(
        kernel=kernel,
        bootloader=None if bootloader_type is None else type('obj', (object,), {'type': bootloader_type}),
        disks=[
            type('obj', (object,), {
                'id': 'disk0',
                'device': '/dev/sda',
                'partitions': [
                    type('obj', (object,), {'mount': '/', 'device': '/dev/sda1'}),
                    type('obj', (object,), {'mount': '/boot', 'device': '/dev/sda2'}),
                ]
            })()
        ]
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_installkernel_binary_true_emerges_bin():
    runner = _FakeRunner()
    execute(_cfg("installkernel", binary=True), runner)
    # Should have at least 6 commands: mkdir (package.use), echo (package.use), mkdir (dracut), echo (dracut), mkdir (cmdline.d), ln (cmdline), emerge
    assert len(runner.shell_commands) >= 6
    # First command: create package.use directory
    assert any("mkdir -p" in cmd and "package.use" in cmd for cmd, _ in runner.shell_commands)
    # Second command: write package.use file with dracut
    assert any("sys-kernel/installkernel dracut" in cmd for cmd, _ in runner.shell_commands)
    # Third command: create dracut config directory
    assert any("dracut.conf.d" in cmd for cmd, _ in runner.shell_commands)
    # Fourth command: write dracut config file
    assert any("kernel_cmdline=" in cmd and "root=UUID=" in cmd for cmd, _ in runner.shell_commands)
    # Last command: emerge gentoo-kernel-bin
    cmd, _ = runner.shell_commands[-1]
    assert "sys-kernel/gentoo-kernel-bin" in cmd
    print("PASS  installkernel binary=True emerges sys-kernel/gentoo-kernel-bin")


def test_installkernel_binary_defaults_to_true():
    runner = _FakeRunner()
    execute(_cfg("installkernel"), runner)
    # Should have at least 6 commands: mkdir (package.use), echo (package.use), mkdir (dracut), echo (dracut), mkdir (cmdline.d), ln (cmdline), emerge
    assert len(runner.shell_commands) >= 6
    # First command: create package.use directory
    assert any("mkdir -p" in cmd and "package.use" in cmd for cmd, _ in runner.shell_commands)
    # Second command: write package.use file with dracut
    assert any("sys-kernel/installkernel dracut" in cmd for cmd, _ in runner.shell_commands)
    # Third command: create dracut config directory
    assert any("dracut.conf.d" in cmd for cmd, _ in runner.shell_commands)
    # Fourth command: write dracut config file
    assert any("kernel_cmdline=" in cmd and "root=UUID=" in cmd for cmd, _ in runner.shell_commands)
    # Last command: emerge gentoo-kernel-bin
    cmd, _ = runner.shell_commands[-1]
    assert "sys-kernel/gentoo-kernel-bin" in cmd
    print("PASS  installkernel binary=None defaults to gentoo-kernel-bin")


def test_installkernel_binary_false_emerges_source():
    runner = _FakeRunner()
    execute(_cfg("installkernel", binary=False), runner)
    # Should have at least 6 commands: mkdir (package.use), echo (package.use), mkdir (dracut), echo (dracut), mkdir (cmdline.d), ln (cmdline), emerge
    assert len(runner.shell_commands) >= 6
    # First command: create package.use directory
    assert any("mkdir -p" in cmd and "package.use" in cmd for cmd, _ in runner.shell_commands)
    # Second command: write package.use file with dracut
    assert any("sys-kernel/installkernel dracut" in cmd for cmd, _ in runner.shell_commands)
    # Third command: create dracut config directory
    assert any("dracut.conf.d" in cmd for cmd, _ in runner.shell_commands)
    # Fourth command: write dracut config file
    assert any("kernel_cmdline=" in cmd and "root=UUID=" in cmd for cmd, _ in runner.shell_commands)
    # Last command: emerge gentoo-kernel (source)
    cmd, _ = runner.shell_commands[-1]
    assert "sys-kernel/gentoo-kernel-bin" not in cmd
    assert "sys-kernel/gentoo-kernel" in cmd
    print("PASS  installkernel binary=False emerges sys-kernel/gentoo-kernel (source)")


def test_installkernel_emerge_runs_in_chroot():
    runner = _FakeRunner()
    execute(_cfg("installkernel"), runner)
    # All commands except blkid should run in chroot
    # blkid runs outside chroot because device is not mounted inside
    for cmd, chroot in runner.shell_commands:
        if "blkid" not in cmd:
            assert chroot is True, f"Command '{cmd}' should run in chroot"
    # blkid should run outside chroot
    blkid_cmd = [(cmd, chroot) for cmd, chroot in runner.shell_commands if "blkid" in cmd]
    assert len(blkid_cmd) == 1, "Should have exactly one blkid command"
    assert blkid_cmd[0][1] is False, "blkid should run outside chroot"
    print("PASS  installkernel emerge runs with chroot=True (except blkid)")


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
    # Should have at least 6 commands: mkdir (package.use), echo (package.use), mkdir (dracut), echo (dracut), mkdir (cmdline.d), ln (cmdline), emerge
    assert len(runner.shell_commands) >= 6
    # Last command should be emerge
    cmd, chroot = runner.shell_commands[-1]
    assert "gentoo-kernel" in cmd
    assert chroot is True
    print("PASS  dry_run mode records all commands without executing")


def test_no_firmware_flags_no_second_emerge():
    runner = _FakeRunner()
    execute(_cfg("installkernel"), runner)
    # Should have at least 6 commands: mkdir (package.use), echo (package.use), mkdir (dracut), echo (dracut), mkdir (cmdline.d), ln (cmdline), emerge
    # Only one emerge command (no firmware packages)
    emerge_count = sum(1 for cmd, _ in runner.shell_commands if "emerge" in cmd)
    assert emerge_count == 1, f"Expected 1 emerge command, got {emerge_count}"
    print("PASS  no firmware flags → only one emerge command")


def test_linux_firmware_emerges_package():
    runner = _FakeRunner()
    execute(_cfg("installkernel", linux_firmware=True), runner)
    # Should have 7 commands: mkdir (package.use), echo (package.use), blkid, mkdir (dracut), echo (dracut), emerge (kernel), emerge (firmware)
    assert len(runner.shell_commands) >= 7
    # Should have 2 emerge commands: kernel and firmware
    emerge_count = sum(1 for cmd, _ in runner.shell_commands if "emerge" in cmd)
    assert emerge_count == 2, f"Expected 2 emerge commands, got {emerge_count}"
    # Last command should be firmware
    cmd, _ = runner.shell_commands[-1]
    assert "sys-kernel/linux-firmware" in cmd
    print("PASS  linux_firmware=True emerges sys-kernel/linux-firmware")


def test_intel_microcode_emerges_package():
    runner = _FakeRunner()
    execute(_cfg("installkernel", intel_microcode=True), runner)
    # Should have 7 commands: mkdir (package.use), echo (package.use), blkid, mkdir (dracut), echo (dracut), emerge (kernel), emerge (firmware)
    assert len(runner.shell_commands) >= 7
    # Should have 2 emerge commands: kernel and firmware
    emerge_count = sum(1 for cmd, _ in runner.shell_commands if "emerge" in cmd)
    assert emerge_count == 2, f"Expected 2 emerge commands, got {emerge_count}"
    # Last command should be firmware
    cmd, _ = runner.shell_commands[-1]
    assert "sys-firmware/intel-microcode" in cmd
    print("PASS  intel_microcode=True emerges sys-firmware/intel-microcode")


def test_sof_firmware_emerges_package():
    runner = _FakeRunner()
    execute(_cfg("installkernel", sof_firmware=True), runner)
    # Should have 7 commands: mkdir (package.use), echo (package.use), blkid, mkdir (dracut), echo (dracut), emerge (kernel), emerge (firmware)
    assert len(runner.shell_commands) >= 7
    # Should have 2 emerge commands: kernel and firmware
    emerge_count = sum(1 for cmd, _ in runner.shell_commands if "emerge" in cmd)
    assert emerge_count == 2, f"Expected 2 emerge commands, got {emerge_count}"
    # Last command should be firmware
    cmd, _ = runner.shell_commands[-1]
    assert "sys-firmware/sof-firmware" in cmd
    print("PASS  sof_firmware=True emerges sys-firmware/sof-firmware")


def test_multiple_firmware_in_single_emerge():
    runner = _FakeRunner()
    execute(_cfg("installkernel", linux_firmware=True, intel_microcode=True, sof_firmware=True), runner)
    # Should have 7 commands: mkdir (package.use), echo (package.use), blkid, mkdir (dracut), echo (dracut), emerge (kernel), emerge (firmware)
    assert len(runner.shell_commands) >= 7
    # Should have 2 emerge commands: kernel and firmware
    emerge_count = sum(1 for cmd, _ in runner.shell_commands if "emerge" in cmd)
    assert emerge_count == 2, f"Expected 2 emerge commands, got {emerge_count}"
    # Last command should have all firmware packages
    cmd, chroot = runner.shell_commands[-1]
    assert "sys-kernel/linux-firmware" in cmd
    assert "sys-firmware/intel-microcode" in cmd
    assert "sys-firmware/sof-firmware" in cmd
    assert chroot is True
    print("PASS  multiple firmware flags → single emerge with all packages")


def test_dist_kernel_always_enabled_for_installkernel():
    """dist-kernel is an installkernel implementation detail — always added."""
    runner = _FakeRunner()
    execute(_cfg("installkernel", binary=True), runner)
    makeconf_use_cmds = [cmd for cmd, _ in runner.shell_commands if "dist-kernel" in cmd]
    assert len(makeconf_use_cmds) >= 1, "Expected dist-kernel in make.conf USE"
    print("PASS  dist-kernel USE flag always enabled for installkernel")


def test_dist_kernel_not_added_for_binary_source():
    """dist-kernel applies to all installkernel variants (binary and source)."""
    runner = _FakeRunner()
    execute(_cfg("installkernel", binary=False), runner)
    makeconf_use_cmds = [cmd for cmd, _ in runner.shell_commands if "dist-kernel" in cmd]
    assert len(makeconf_use_cmds) >= 1, "Expected dist-kernel for source kernel too"
    print("PASS  dist-kernel USE flag enabled for source kernels too")


def test_modules_sign_default_enabled_for_source():
    """modules-sign should be added for binary=False kernels by default."""
    runner = _FakeRunner()
    execute(_cfg("installkernel", binary=False), runner)
    makeconf_use_cmds = [cmd for cmd, _ in runner.shell_commands if "modules-sign" in cmd]
    assert len(makeconf_use_cmds) >= 1, "Expected modules-sign in make.conf USE for source kernel"
    print("PASS  modules-sign USE flag enabled by default for source kernels")


def test_modules_sign_not_added_for_binary():
    """modules-sign should NOT be added for binary=True kernels."""
    runner = _FakeRunner()
    execute(_cfg("installkernel", binary=True), runner)
    makeconf_use_cmds = [cmd for cmd, _ in runner.shell_commands if "modules-sign" in cmd]
    assert len(makeconf_use_cmds) == 0, "Expected no modules-sign for binary kernel"
    print("PASS  modules-sign not added for binary kernels")


def test_modules_sign_with_keys():
    """MODULES_SIGN_KEY and MODULES_SIGN_CERT should be written when provided."""
    runner = _FakeRunner()
    execute(_cfg("installkernel", binary=False, sign_key="/etc/kernel_key.pem",
                 sign_cert="/etc/kernel_key.pem", sign_hash="sha512"), runner)
    cmds = [cmd for cmd, _ in runner.shell_commands]
    assert any("MODULES_SIGN_KEY" in cmd for cmd in cmds), "Expected MODULES_SIGN_KEY in make.conf"
    assert any("MODULES_SIGN_CERT" in cmd for cmd in cmds), "Expected MODULES_SIGN_CERT in make.conf"
    assert any("MODULES_SIGN_HASH" in cmd for cmd in cmds), "Expected MODULES_SIGN_HASH in make.conf"
    print("PASS  MODULES_SIGN_KEY/CERT/HASH written to make.conf")


def test_secureboot_disabled_by_default():
    """secureboot should NOT be added by default."""
    runner = _FakeRunner()
    execute(_cfg("installkernel", binary=False), runner)
    makeconf_use_cmds = [cmd for cmd, _ in runner.shell_commands if "secureboot" in cmd]
    assert len(makeconf_use_cmds) == 0, "Expected no secureboot by default"
    print("PASS  secureboot disabled by default")


def test_secureboot_explicit_true_enables():
    """secureboot=True should add the flag and signing keys."""
    runner = _FakeRunner()
    execute(_cfg("installkernel", binary=False, secureboot=True,
                 sign_key="/etc/kernel_key.pem", sign_cert="/etc/kernel_key.pem"), runner)
    cmds = [cmd for cmd, _ in runner.shell_commands]
    assert any("secureboot" in cmd and "USE=" in cmd for cmd in cmds), "Expected secureboot in make.conf USE"
    assert any("SECUREBOOT_SIGN_KEY" in cmd for cmd in cmds), "Expected SECUREBOOT_SIGN_KEY in make.conf"
    assert any("SECUREBOOT_SIGN_CERT" in cmd for cmd in cmds), "Expected SECUREBOOT_SIGN_CERT in make.conf"
    print("PASS  secureboot=True adds flag and keys")


def test_secureboot_not_added_for_binary():
    """secureboot should NOT be added for binary=True even if explicitly set."""
    runner = _FakeRunner()
    execute(_cfg("installkernel", binary=True, secureboot=True), runner)
    makeconf_use_cmds = [cmd for cmd, _ in runner.shell_commands if "secureboot" in cmd]
    assert len(makeconf_use_cmds) == 0, "Expected no secureboot for binary kernel"
    print("PASS  secureboot not added for binary kernels")


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
    test_dist_kernel_always_enabled_for_installkernel()
    test_dist_kernel_not_added_for_binary_source()
    test_modules_sign_default_enabled_for_source()
    test_modules_sign_not_added_for_binary()
    test_modules_sign_with_keys()
    test_secureboot_disabled_by_default()
    test_secureboot_explicit_true_enables()
    test_secureboot_not_added_for_binary()
    print("\nAll kernel tests passed.")
