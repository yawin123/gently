"""
Unit tests for installer.runner.

Run with:
    python3 tests/test_runner.py
"""

from __future__ import annotations

import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "vendor"))

from installer.runner import (
    CommandExecutionError,
    CommandSpec,
    LocalRunner,
    SshRunner,
    build_runner,
)


class _CP:
    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = ""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_local_runner_dry_run():
    runner = LocalRunner(dry_run=True)
    result = runner.run(CommandSpec(argv=["echo", "hello"]))
    assert result.skipped is True
    assert result.returncode == 0
    assert result.transport == "local"
    print("PASS  LocalRunner dry-run")


def test_local_runner_executes_command():
    runner = LocalRunner()
    result = runner.run(CommandSpec(argv=["bash", "-lc", "echo hello"]))
    assert result.returncode == 0
    assert "hello" in result.stdout
    print("PASS  LocalRunner executes command")


def test_local_runner_raises_on_failure():
    runner = LocalRunner()
    try:
        runner.run(CommandSpec(argv=["bash", "-lc", "exit 7"]))
    except CommandExecutionError as exc:
        assert exc.result.returncode == 7
        print("PASS  LocalRunner raises on failure")
        return
    raise AssertionError("Expected CommandExecutionError")


def test_ssh_runner_starts_single_session_and_reuses():
    calls: list[list[str]] = []

    def fake_run(argv, **kwargs):
        calls.append(list(argv))
        return _CP(0, stdout="ok", stderr="")

    runner = SshRunner("root@10.0.0.2", run_impl=fake_run)
    runner.run(CommandSpec(argv=["echo", "one"]))
    runner.run(CommandSpec(argv=["echo", "two"], cwd="/tmp"))
    runner.close()

    # 1) open control session, 2-3) command calls, 4) close session
    assert len(calls) == 4, calls
    assert "-MNf" in calls[0], calls[0]
    assert calls[1][0] == "ssh"
    assert calls[2][0] == "ssh"
    assert "-O" in calls[3] and "exit" in calls[3], calls[3]
    print("PASS  SshRunner opens/reuses/closes control session")


def test_build_runner_factory():
    local = build_runner("local", dry_run=True)
    assert local.transport == "local"

    ssh = build_runner("ssh:root@127.0.0.1", dry_run=True)
    assert ssh.transport.startswith("ssh:")
    print("PASS  build_runner factory")


if __name__ == "__main__":
    test_local_runner_dry_run()
    test_local_runner_executes_command()
    test_local_runner_raises_on_failure()
    test_ssh_runner_starts_single_session_and_reuses()
    test_build_runner_factory()
    print()
    print("All runner tests passed.")
