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
from installer.stage3 import ensure_stage3_space
from installer.runner import CommandExecutionError, CommandResult, CommandSpec
from model.config import DiskConfig, GentlyConfig, Stage3Config


class _FakeRunner:
    transport = "local"

    def __init__(self, *, free_bytes: int, readable_paths: set[str] | None = None, file_sizes: dict[str, int] | None = None):
        self.free_bytes = free_bytes
        self.readable_paths = readable_paths if readable_paths is not None else set()
        self.file_sizes = dict(file_sizes or {})
        self.shell_commands: list[str] = []

    def run_shell(self, command: str, check: bool = True, cwd: str | None = None, env: dict[str, str] | None = None, phase: str | None = None) -> CommandResult:
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


if __name__ == "__main__":
    test_stage3_space_succeeds_after_root_mount()
    test_stage3_space_rejects_too_small_mountpoint()
    print()
    print("All stage3 tests passed.")
