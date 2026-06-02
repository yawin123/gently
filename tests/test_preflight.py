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
        cached_stage3: bool = False,
        cached_signature: bool = False,
        gpg_auto_retrieve_ok: bool = True,
    ):
        self.free_bytes = free_bytes
        self.mounted_output = mounted_output
        self.readable_paths = readable_paths if readable_paths is not None else set()
        self.file_sizes = dict(file_sizes or {})
        self.dry_run = dry_run
        self.cached_stage3 = cached_stage3
        self.cached_signature = cached_signature
        self.gpg_auto_retrieve_ok = gpg_auto_retrieve_ok
        self.commands: list[CommandSpec] = []
        self.shell_commands: list[str] = []

    def run(self, spec: CommandSpec) -> CommandResult:
        self.commands.append(spec)
        argv_str = " ".join(spec.argv)

        rc = 0
        out = ""
        err = ""

        # Detect python3 -c "import urllib.request..." download commands
        if len(spec.argv) >= 3 and spec.argv[0] == "python3" and spec.argv[1] == "-c":
            code = spec.argv[2]
            if "urllib.request.urlretrieve" in code:
                out = ""

        result = CommandResult(
            argv=spec.argv,
            returncode=rc,
            stdout=out,
            stderr=err,
            duration_sec=0.0,
            transport=self.transport,
            phase=spec.phase,
        )
        if spec.check and rc != 0:
            raise CommandExecutionError(spec, result)
        return result

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
            base = "/tmp/gently-stage3.tar.xz"
            if command.startswith(f"test -s {base}.asc"):
                out = "yes\n" if self.cached_signature else "no\n"
            elif command.startswith(f"test -s {base} "):
                out = "yes\n" if self.cached_stage3 else "no\n"
            else:
                out = "yes\n" if self.cached_stage3 else "no\n"
        elif command.startswith("cat /tmp/gently-stage3-latest.txt"):
            out = "20250101T170000Z/stage3-amd64-openrc-20250101T170000Z.tar.xz  123456789\n"
        elif command.startswith("gpg --auto-key-retrieve --verify"):
            rc = 0 if self.gpg_auto_retrieve_ok else 1
            if not self.gpg_auto_retrieve_ok:
                err = "gpg: using RSA key 1234567890ABCDEF\n"
        elif command.startswith("gpg --keyserver"):
            rc = 0
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
    """When local_path is unreadable, preflight clears it and falls through
    to auto-download instead of crashing."""
    cfg = GentlyConfig(
        stage3=Stage3Config(local_path="/tmp/stage3.tar.xz"),
        disks=[DiskConfig(device="/dev/sda")],
    )
    runner = _FakeRunner(readable_paths=set())

    execute(cfg, runner)

    # local_path was cleared; auto-download kicked in.
    assert cfg.stage3 is not None
    assert cfg.stage3.local_path is None or cfg.stage3.local_path == STAGE3_CACHE
    print("PASS  preflight handles unreadable stage3 local_path by falling back to auto-download")


def _has_download_cmd(runner: _FakeRunner) -> bool:
    """Check if any command contains a urllib download (run or run_shell)."""
    for cmd in runner.commands:
        argv_str = " ".join(cmd.argv)
        if "urlretrieve" in argv_str:
            return True
    for cmd in runner.shell_commands:
        if "urllib.request.urlretrieve" in cmd:
            return True
    return False


def test_stage3_auto_download_sets_local_path():
    cfg = GentlyConfig(
        stage3=Stage3Config(),
        disks=[DiskConfig(device="/dev/sda")],
    )
    runner = _FakeRunner()
    assert cfg.stage3.local_path is None

    execute(cfg, runner)

    assert cfg.stage3.local_path == STAGE3_CACHE
    assert _has_download_cmd(runner), runner.commands + runner.shell_commands
    print("PASS  stage3 auto-download sets local_path")


def test_stage3_download_idempotent():
    cfg = GentlyConfig(
        stage3=Stage3Config(verify_signature=False),
        disks=[DiskConfig(device="/dev/sda")],
    )
    runner = _FakeRunner(cached_stage3=True)

    execute(cfg, runner)

    assert cfg.stage3.local_path == STAGE3_CACHE
    # No download commands should have been issued.
    assert not _has_download_cmd(runner), runner.commands + runner.shell_commands
    # No GPG verify/fetch commands either since verify_signature=False
    gpg_cmds = [c for c in runner.shell_commands if c.startswith("gpg --")]
    assert not gpg_cmds, gpg_cmds
    print("PASS  stage3 download is idempotent")


def test_stage3_cached_with_verify_rechecks_gpg():
    """When cached and verify_signature=True, re-verify the signature."""
    cfg = GentlyConfig(
        stage3=Stage3Config(verify_signature=True),
        disks=[DiskConfig(device="/dev/sda")],
    )
    runner = _FakeRunner(cached_stage3=True, cached_signature=True)

    execute(cfg, runner)

    assert cfg.stage3.local_path == STAGE3_CACHE
    # No download commands should have been issued.
    assert not _has_download_cmd(runner), runner.commands + runner.shell_commands
    # GPG verification should have run on the cached file.
    gpg_cmds = [c for c in runner.shell_commands if c.startswith("gpg --")]
    assert any("gpg --auto-key-retrieve --verify" in c for c in gpg_cmds), gpg_cmds
    print("PASS  cached stage3 re-verifies GPG signature")


def test_stage3_tarball_url_download():
    """When tarball_url is set, download from that URL."""
    cfg = GentlyConfig(
        stage3=Stage3Config(
            tarball_url="https://example.com/stage3-custom.tar.xz",
            verify_signature=False,
        ),
        disks=[DiskConfig(device="/dev/sda")],
    )
    runner = _FakeRunner()

    execute(cfg, runner)

    assert cfg.stage3.local_path == STAGE3_CACHE
    assert _has_download_cmd(runner), runner.commands + runner.shell_commands
    print("PASS  stage3 tarball_url download works")


def test_stage3_signature_url_download():
    """When signature_url is set, download and verify with that signature."""
    cfg = GentlyConfig(
        stage3=Stage3Config(
            tarball_url="https://example.com/stage3.tar.xz",
            signature_url="https://example.com/stage3.tar.xz.asc",
            verify_signature=True,
        ),
        disks=[DiskConfig(device="/dev/sda")],
    )
    runner = _FakeRunner()

    execute(cfg, runner)

    assert cfg.stage3.local_path == STAGE3_CACHE
    # GPG verification should have run (with signature downloaded)
    gpg_cmds = [c for c in runner.shell_commands if c.startswith("gpg --")]
    assert gpg_cmds, runner.shell_commands
    print("PASS  stage3 signature_url download works")


def test_stage3_signature_path_verification():
    """When signature_path is set, verify using that local file."""
    cfg = GentlyConfig(
        stage3=Stage3Config(
            tarball_url="https://example.com/stage3.tar.xz",
            signature_path="/path/to/signature.asc",
            verify_signature=True,
        ),
        disks=[DiskConfig(device="/dev/sda")],
    )
    runner = _FakeRunner(readable_paths={"/path/to/signature.asc"})

    execute(cfg, runner)

    assert cfg.stage3.local_path == STAGE3_CACHE
    gpg_cmds = [c for c in runner.shell_commands if c.startswith("gpg --")]
    assert gpg_cmds, runner.shell_commands
    print("PASS  stage3 signature_path verification works")


def test_stage3_dry_run_skips_download_sets_placeholder():
    cfg = GentlyConfig(
        stage3=Stage3Config(),
        disks=[DiskConfig(device="/dev/sda")],
    )
    runner = _FakeRunner(dry_run=True)

    execute(cfg, runner)

    # In dry-run, local_path is set to a placeholder so downstream phases
    # print realistic commands, but no real downloads are attempted.
    assert cfg.stage3.local_path == STAGE3_CACHE
    assert not _has_download_cmd(runner), runner.commands + runner.shell_commands
    print("PASS  stage3 dry-run skips download and sets placeholder path")


if __name__ == "__main__":
    test_preflight_success_runs_checks()
    test_preflight_rejects_mounted_disk()
    test_preflight_requires_readable_stage3_local_path()
    test_stage3_auto_download_sets_local_path()
    test_stage3_download_idempotent()
    test_stage3_cached_with_verify_rechecks_gpg()
    test_stage3_tarball_url_download()
    test_stage3_signature_url_download()
    test_stage3_signature_path_verification()
    test_stage3_dry_run_skips_download_sets_placeholder()
    print()
    print("All preflight tests passed.")
