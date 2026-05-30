"""
Contract tests for installer.stage3 helpers.

Run with:
    python3 tests/test_stage3.py
"""

from __future__ import annotations

import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "vendor"))

from installer.preflight import PreflightError
from installer.stage3 import ensure_stage3_space, execute, _resolve_tarball, Stage3Error
from installer.partition import FSTAB_STAGING_PATH, MOUNTPOINT
from installer.runner import CommandExecutionError, CommandResult, CommandSpec
from model.config import DiskConfig, GentlyConfig, Stage3Config
import i18n as _i18n


class _FakeRunner:
    transport = "local"

    def __init__(
        self,
        *,
        dry_run: bool = False,
        free_bytes: int = 10 * 1024 ** 3,
        readable_paths: set[str] | None = None,
        file_sizes: dict[str, int] | None = None,
    ):
        self.dry_run = dry_run
        self.free_bytes = free_bytes
        self.readable_paths = readable_paths if readable_paths is not None else set()
        self.file_sizes = dict(file_sizes or {})
        self.shell_commands: list[str] = []
        self.log_callback = None
        self.confirm_callback = None

    def run_shell(self, command: str, check: bool = True, cwd: str | None = None, env: dict[str, str] | None = None, phase: str | None = None) -> CommandResult:
        if self.dry_run:
            return CommandResult(
                argv=["bash", "-lc", command],
                returncode=0, stdout="", stderr="",
                duration_sec=0.0, transport=self.transport, phase=phase,
            )
        self.shell_commands.append(command)

        rc = 0
        out = ""
        err = ""

        if command.startswith('python3 -c "import shutil; print(shutil.disk_usage('):
            out = f"{self.free_bytes}\n"
        elif command.startswith("test -r "):
            path = command[len("test -r "):].strip().strip("'")
            if path not in self.readable_paths:
                rc = 1
                err = "not readable"
        elif command.startswith("stat -c %s "):
            path = command[len("stat -c %s "):].strip().strip("'")
            if path in self.file_sizes:
                out = f"{self.file_sizes[path]}\n"
            else:
                rc = 1
                err = "not found"

        result = CommandResult(
            argv=["bash", "-lc", command],
            returncode=rc,
            stdout=out,
            stderr=err,
            duration_sec=0.0,
            transport=self.transport,
            phase=phase,
        )
        if check and rc != 0:
            raise CommandExecutionError(CommandSpec(argv=result.argv, check=check, phase=phase), result)
        return result


def test_stage3_space_succeeds_after_root_mount():
    cfg = GentlyConfig(stage3=Stage3Config(local_path="/tmp/stage3.tar.xz"), disks=[DiskConfig(device="/dev/sda")])
    runner = _FakeRunner(
        free_bytes=8 * 1024 * 1024 * 1024,
        readable_paths={"/tmp/stage3.tar.xz"},
        file_sizes={"/tmp/stage3.tar.xz": 400 * 1024 * 1024},
    )

    ensure_stage3_space(cfg, runner, mountpoint="/mnt/gentoo")

    assert any(cmd.startswith('python3 -c "import shutil; print(shutil.disk_usage(') for cmd in runner.shell_commands), runner.shell_commands
    print("PASS  stage3 space check works after mount")


def test_stage3_space_rejects_too_small_mountpoint():
    cfg = GentlyConfig(stage3=Stage3Config(local_path="/tmp/stage3.tar.xz"), disks=[DiskConfig(device="/dev/sda")])
    runner = _FakeRunner(
        free_bytes=100,
        readable_paths={"/tmp/stage3.tar.xz"},
        file_sizes={"/tmp/stage3.tar.xz": 400 * 1024 * 1024},
    )

    try:
        ensure_stage3_space(cfg, runner, mountpoint="/mnt/gentoo")
    except PreflightError as exc:
        assert "/mnt/gentoo" in str(exc)
        assert "enough free space" in str(exc)
        print("PASS  stage3 space check rejects small mountpoint")
        return
    raise AssertionError("Expected PreflightError")


# ---------------------------------------------------------------------------
# Tests for execute()
# ---------------------------------------------------------------------------

def _default_runner() -> _FakeRunner:
    return _FakeRunner(
        free_bytes=10 * 1024 ** 3,
        readable_paths={"/tmp/stage3.tar.xz"},
        file_sizes={"/tmp/stage3.tar.xz": 100 * 1024 * 1024},
    )


def _config_with_tarball(path: str = "/tmp/stage3.tar.xz") -> GentlyConfig:
    return GentlyConfig(stage3=Stage3Config(local_path=path))


def test_execute_raises_when_no_tarball():
    config = GentlyConfig(stage3=Stage3Config())  # local_path=None
    runner = _default_runner()
    try:
        execute(config, runner)
    except Stage3Error as exc:
        assert str(exc) == _i18n.t("stage3_error_no_tarball"), repr(str(exc))
        print("PASS  execute raises Stage3Error when local_path is not set")
        return
    raise AssertionError("Expected Stage3Error")


def test_execute_raises_when_stage3_is_none():
    config = GentlyConfig()  # stage3=None
    runner = _default_runner()
    try:
        execute(config, runner)
    except Stage3Error:
        print("PASS  execute raises Stage3Error when stage3 config is None")
        return
    raise AssertionError("Expected Stage3Error")


def test_execute_calls_tar_with_correct_flags():
    config = _config_with_tarball()
    runner = _default_runner()
    execute(config, runner)
    tar_cmds = [c for c in runner.shell_commands if "tar " in c]
    assert len(tar_cmds) == 1, f"Expected 1 tar command, got: {tar_cmds}"
    cmd = tar_cmds[0]
    assert "xpvf" in cmd, cmd
    assert "/tmp/stage3.tar.xz" in cmd, cmd
    assert MOUNTPOINT in cmd, cmd
    assert "--xattrs-include='*.*'" in cmd, cmd
    assert "--numeric-owner" in cmd, cmd
    print("PASS  execute calls tar xpf with xattrs and numeric-owner")


def test_execute_copies_staged_fstab():
    config = _config_with_tarball()
    runner = _default_runner()
    execute(config, runner)
    cp_cmds = [c for c in runner.shell_commands if c.startswith("cp ")]
    assert len(cp_cmds) == 1, f"Expected 1 cp command, got: {cp_cmds}"
    cmd = cp_cmds[0]
    assert FSTAB_STAGING_PATH in cmd, cmd
    assert f"{MOUNTPOINT}/etc/fstab" in cmd, cmd
    print("PASS  execute copies staged fstab to /mnt/gentoo/etc/fstab")


def test_execute_copies_fstab_after_tar():
    """cp must come after tar so /mnt/gentoo/etc/ already exists."""
    config = _config_with_tarball()
    runner = _default_runner()
    execute(config, runner)
    tar_idx = next(i for i, c in enumerate(runner.shell_commands) if "tar " in c)
    cp_idx = next(i for i, c in enumerate(runner.shell_commands) if c.startswith("cp "))
    assert tar_idx < cp_idx, f"cp (idx={cp_idx}) must come after tar (idx={tar_idx})"
    print("PASS  execute copies fstab after tar extraction")


def test_execute_dry_run_skips_commands():
    config = _config_with_tarball()
    runner = _FakeRunner(dry_run=True)
    execute(config, runner)
    assert not runner.shell_commands, runner.shell_commands
    print("PASS  execute issues no real commands in dry-run mode")


def test_execute_dry_run_without_tarball_does_not_raise():
    """dry_run must succeed even when no tarball is configured (UI preview mode)."""
    config = GentlyConfig()  # stage3=None, no local_path
    runner = _FakeRunner(dry_run=True)
    execute(config, runner)  # must not raise
    assert not runner.shell_commands
    print("PASS  execute dry-run succeeds without any tarball configured")


def test_resolve_tarball_raises_when_not_configured():
    try:
        _resolve_tarball(GentlyConfig(), _FakeRunner(dry_run=False))
    except Stage3Error:
        print("PASS  _resolve_tarball raises Stage3Error when not configured")
        return
    raise AssertionError("Expected Stage3Error")


def test_resolve_tarball_returns_placeholder_in_dry_run():
    path = _resolve_tarball(GentlyConfig(), _FakeRunner(dry_run=True))
    assert path  # any non-empty string is fine as a placeholder
    print("PASS  _resolve_tarball returns placeholder in dry_run")


def test_resolve_tarball_returns_configured_path():
    config = _config_with_tarball("/mnt/iso/stage3.tar.xz")
    path = _resolve_tarball(config, _FakeRunner(dry_run=False))
    assert path == "/mnt/iso/stage3.tar.xz", path
    print("PASS  _resolve_tarball returns configured local_path")


if __name__ == "__main__":
    test_stage3_space_succeeds_after_root_mount()
    test_stage3_space_rejects_too_small_mountpoint()
    test_execute_raises_when_no_tarball()
    test_execute_raises_when_stage3_is_none()
    test_execute_calls_tar_with_correct_flags()
    test_execute_copies_staged_fstab()
    test_execute_copies_fstab_after_tar()
    test_execute_dry_run_skips_commands()
    test_execute_dry_run_without_tarball_does_not_raise()
    test_resolve_tarball_raises_when_not_configured()
    test_resolve_tarball_returns_placeholder_in_dry_run()
    test_resolve_tarball_returns_configured_path()
    print()
    print("All stage3 tests passed.")
