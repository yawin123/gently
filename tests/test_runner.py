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
    RunnerError,
    SshRunner,
    build_runner,
    default_install_phases,
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
    assert "StrictHostKeyChecking=no" in calls[0], calls[0]
    assert "UserKnownHostsFile=/dev/null" in calls[0], calls[0]
    assert "GlobalKnownHostsFile=/dev/null" in calls[0], calls[0]
    assert calls[1][0] == "ssh"
    assert calls[2][0] == "ssh"
    assert "-O" in calls[3] and "exit" in calls[3], calls[3]
    print("PASS  SshRunner opens/reuses/closes control session")


def test_ssh_runner_password_uses_sshpass():
    calls: list[tuple[list[str], dict | None]] = []

    def fake_run(argv, **kwargs):
        calls.append((list(argv), kwargs.get("env")))
        return _CP(0, stdout="ok", stderr="")

    runner = SshRunner("root@10.0.0.2", password="toor", run_impl=fake_run)
    runner.run(CommandSpec(argv=["echo", "one"]))
    runner.close()

    open_cmd, open_env = calls[0]
    run_cmd, run_env = calls[1]
    close_cmd, close_env = calls[2]

    assert open_cmd[:2] == ["sshpass", "-e"], open_cmd
    assert run_cmd[:2] == ["sshpass", "-e"], run_cmd
    assert close_cmd[:2] == ["sshpass", "-e"], close_cmd
    assert open_env is not None and open_env.get("SSHPASS") == "toor"
    assert run_env is not None and run_env.get("SSHPASS") == "toor"
    assert close_env is not None and close_env.get("SSHPASS") == "toor"
    print("PASS  SshRunner password mode wraps ssh with sshpass")


def test_ssh_runner_password_requires_sshpass_binary():
    runner = SshRunner("root@10.0.0.2", password="toor")
    import installer.runner as runner_mod
    original_which = runner_mod.shutil.which
    runner_mod.shutil.which = lambda _name: None

    try:
        runner._with_auth(["ssh", "root@10.0.0.2", "true"])
    except RunnerError as exc:
        assert "sshpass" in str(exc)
        print("PASS  SshRunner password mode requires sshpass")
    else:
        raise AssertionError("Expected RunnerError when sshpass is missing")
    finally:
        runner_mod.shutil.which = original_which


def test_build_runner_factory():
    local = build_runner("local", dry_run=True)
    assert local.transport == "local"

    ssh = build_runner("ssh:root@127.0.0.1", dry_run=True)
    assert ssh.transport.startswith("ssh:")

    ssh_with_password = build_runner("ssh:root@127.0.0.1", dry_run=True, ssh_password="toor")
    assert isinstance(ssh_with_password, SshRunner)
    assert ssh_with_password.password == "toor"
    print("PASS  build_runner factory")


def test_default_install_phases_order():
    phases = default_install_phases()
    keys = [p.key for p in phases]
    assert keys == [
        "preflight",
        "partition",
        "stage3",
        "portage",
        "kernel",
        "system",
        "services",
        "users",
        "bootloader",
        "packages",
    ], keys
    print("PASS  default_install_phases order")


if __name__ == "__main__":
    test_local_runner_dry_run()
    test_local_runner_executes_command()
    test_local_runner_raises_on_failure()
    test_ssh_runner_starts_single_session_and_reuses()
    test_ssh_runner_password_uses_sshpass()
    test_ssh_runner_password_requires_sshpass_binary()
    test_build_runner_factory()
    test_default_install_phases_order()
    print()
    print("All runner tests passed.")
