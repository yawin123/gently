"""
Contract tests for installer.preflight.

Run with:
    python3 tests/test_preflight.py
"""

from __future__ import annotations

import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "vendor"))

from installer.preflight import PreflightError, STAGE3_CACHE, execute
from installer.runner import CommandExecutionError, CommandResult, CommandSpec
from model.config import DiskConfig, GentlyConfig, Stage3Config


class _FakeRunner:
    transport = "local"

    def __init__(
        self,
        *,
        free_bytes: int = 8 * 1024 * 1024 * 1024,
        mounted_output: str = "",
        readable_paths: set[str] | None = None,
        file_sizes: dict[str, int] | None = None,
        dry_run: bool = False,
    ):
        self.free_bytes = free_bytes
        self.mounted_output = mounted_output
        self.readable_paths = readable_paths if readable_paths is not None else set()
        self.file_sizes = dict(file_sizes or {})
        self.dry_run = dry_run
        self.shell_commands: list[str] = []

    def run_shell(
        self,
        command: str,
        check: bool = True,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        phase: str | None = None,
    ) -> CommandResult:
        self.shell_commands.append(command)

        rc = 0
        out = ""
        err = ""

        if command.startswith("df -B1 --output=avail /mnt"):
            out = f"{self.free_bytes}\n"
        elif command.startswith("python3 -c \"import os, shutil;"):
            out = f"{self.free_bytes}\n"
        elif command.startswith("lsblk -nro MOUNTPOINT"):
            out = self.mounted_output
        elif command.startswith("test -b "):
            rc = 0
        elif command.startswith("test -r "):
            path = command[len("test -r "):].strip().strip("'")
            if path not in self.readable_paths:
                rc = 1
                err = "not readable"
        elif command.startswith("test -s /tmp/gently-stage3.tar.xz"):
            out = "no\n"
        elif command.startswith('python3 -c "import urllib.request;'):
            out = ""  # download — no output needed
        elif command.startswith("cat /tmp/gently-stage3-latest.txt"):
            out = "20250101T170000Z/stage3-amd64-openrc-20250101T170000Z.tar.xz  123456789\n"
        elif command.startswith("gpg --auto-key-retrieve --verify"):
            rc = 0  # auto-retrieve succeeds in test
        elif command.startswith("gpg --verify"):
            rc = 0
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


def test_preflight_success_runs_checks():
    cfg = GentlyConfig(
        stage3=Stage3Config(),
        disks=[DiskConfig(device="/dev/sda")],
    )
    runner = _FakeRunner()

    execute(cfg, runner)

    expected = [
        "command -v parted >/dev/null",
        "command -v mkfs.ext4 >/dev/null",
        "command -v mkfs.vfat >/dev/null",
        "command -v tar >/dev/null",
        "command -v gpg >/dev/null",
        "ping -c 1 8.8.8.8 >/dev/null",
        "test -b /dev/sda",
        "lsblk -nro MOUNTPOINT /dev/sda | sed '/^$/d'",
    ]
    for cmd in expected:
        assert cmd in runner.shell_commands, runner.shell_commands
    print("PASS  preflight success checks")


def test_preflight_rejects_mounted_disk():
    cfg = GentlyConfig(disks=[DiskConfig(device="/dev/sda")])
    runner = _FakeRunner(mounted_output="/mnt/gentoo\n")

    try:
        execute(cfg, runner)
    except PreflightError as exc:
        assert "mounted" in str(exc)
        print("PASS  preflight rejects mounted disk")
        return
    raise AssertionError("Expected PreflightError")


def test_preflight_requires_readable_stage3_local_path():
    cfg = GentlyConfig(
        stage3=Stage3Config(local_path="/tmp/stage3.tar.xz"),
        disks=[DiskConfig(device="/dev/sda")],
    )
    runner = _FakeRunner(readable_paths=set())

    try:
        execute(cfg, runner)
    except PreflightError as exc:
        assert "failed" in str(exc).lower() or "readable" in str(exc).lower()
        print("PASS  preflight validates stage3 local_path readability")
        return
    raise AssertionError("Expected PreflightError")


def test_stage3_auto_download_sets_local_path():
    cfg = GentlyConfig(
        stage3=Stage3Config(),
        disks=[DiskConfig(device="/dev/sda")],
    )
    runner = _FakeRunner()
    assert cfg.stage3.local_path is None

    execute(cfg, runner)

    assert cfg.stage3.local_path == STAGE3_CACHE
    assert any(cmd.startswith('python3 -c "import urllib.request;') for cmd in runner.shell_commands), runner.shell_commands
    print("PASS  stage3 auto-download sets local_path")


def test_stage3_download_idempotent():
    cfg = GentlyConfig(
        stage3=Stage3Config(),
        disks=[DiskConfig(device="/dev/sda")],
    )
    runner = _FakeRunner()
    runner.shell_commands = ["test -s /tmp/gently-stage3.tar.xz && echo yes || echo no"]
    # Simulate the cache being already populated by overriding the match.
    class CachedRunner(_FakeRunner):
        def run_shell(self, command, check=True, cwd=None, env=None, phase=None):
            result = super().run_shell(command, check=check, cwd=cwd, env=env, phase=phase)
            if command.startswith("test -s /tmp/gently-stage3.tar.xz"):
                result.stdout = "yes\n"
            return result

    cached = CachedRunner()
    cached.shell_commands = []
    execute(cfg, cached)

    assert cfg.stage3.local_path == STAGE3_CACHE
    # No download commands should have been issued.
    assert not any(cmd.startswith('python3 -c "import urllib.request;') for cmd in cached.shell_commands), cached.shell_commands
    print("PASS  stage3 download is idempotent")


def test_stage3_download_shown_in_dry_run():
    cfg = GentlyConfig(
        stage3=Stage3Config(),
        disks=[DiskConfig(device="/dev/sda")],
    )
    runner = _FakeRunner(dry_run=True)

    execute(cfg, runner)

    # In dry-run, download commands are emitted (shown to the user) but are no-ops.
    assert any(cmd.startswith('python3 -c "import urllib.request;') for cmd in runner.shell_commands), runner.shell_commands
    print("PASS  stage3 download shown in dry-run (no-op)")


if __name__ == "__main__":
    test_preflight_success_runs_checks()
    test_preflight_rejects_mounted_disk()
    test_preflight_requires_readable_stage3_local_path()
    test_stage3_auto_download_sets_local_path()
    test_stage3_download_idempotent()
    test_stage3_download_shown_in_dry_run()
    print()
    print("All preflight tests passed.")
