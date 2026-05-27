"""
Contract tests for installer.run_installation orchestration.

Run with:
    python3 tests/test_installer_orchestrator.py
"""

from __future__ import annotations

import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "vendor"))

from installer.runner import (
    CommandExecutionError,
    CommandResult,
    CommandSpec,
    InstallPhase,
    InstallPhaseError,
    LocalRunner,
    run_installation,
)


def test_orchestrator_runs_phases_in_order():
    order: list[str] = []

    def mk(name: str):
        def _run(_cfg, _runner):
            order.append(name)
        return _run

    phases = [
        InstallPhase("a", "A", mk("a")),
        InstallPhase("b", "B", mk("b")),
        InstallPhase("c", "C", mk("c")),
    ]

    report = run_installation(config={}, runner=LocalRunner(dry_run=True), phases=phases)
    assert order == ["a", "b", "c"], order
    assert report.ok is True
    assert [p.key for p in report.phases] == ["a", "b", "c"]
    print("PASS  orchestrator runs phases in order")


def test_orchestrator_stops_on_first_error():
    order: list[str] = []

    def ok(name: str):
        def _run(_cfg, _runner):
            order.append(name)
        return _run

    def fail(_cfg, _runner):
        order.append("b")
        raise RuntimeError("boom")

    phases = [
        InstallPhase("a", "A", ok("a")),
        InstallPhase("b", "B", fail),
        InstallPhase("c", "C", ok("c")),
    ]

    try:
        run_installation(config={}, runner=LocalRunner(dry_run=True), phases=phases)
    except InstallPhaseError as exc:
        assert exc.phase_key == "b"
        assert order == ["a", "b"], order
        assert [p.status for p in exc.partial_report.phases] == ["ok", "error"]
        print("PASS  orchestrator stops on first error")
        return
    raise AssertionError("Expected InstallPhaseError")


def test_orchestrator_preserves_command_error_context():
    spec = CommandSpec(argv=["false"], phase="partition")
    result = CommandResult(
        argv=spec.argv,
        returncode=1,
        stdout="",
        stderr="simulated error",
        duration_sec=0.01,
        transport="local",
        phase=spec.phase,
    )

    def fail_with_command(_cfg, _runner):
        raise CommandExecutionError(spec, result)

    phases = [InstallPhase("partition", "Partition", fail_with_command)]

    try:
        run_installation(config={}, runner=LocalRunner(dry_run=True), phases=phases)
    except InstallPhaseError as exc:
        assert exc.phase_key == "partition"
        assert isinstance(exc.cause, CommandExecutionError)
        assert "simulated error" in str(exc.cause)
        print("PASS  command error context is preserved")
        return
    raise AssertionError("Expected InstallPhaseError")


def test_orchestrator_calls_progress_callback():
    events: list[tuple[str, str]] = []

    phases = [
        InstallPhase("a", "A", lambda _c, _r: None),
        InstallPhase("b", "B", lambda _c, _r: None),
    ]

    run_installation(
        config={},
        runner=LocalRunner(dry_run=True),
        phases=phases,
        progress_cb=lambda phase, msg: events.append((phase, msg)),
    )

    assert len(events) == 4, events
    assert events[0][0] == "a" and "Starting" in events[0][1]
    assert events[1][0] == "a" and "Completed" in events[1][1]
    assert events[2][0] == "b" and "Starting" in events[2][1]
    assert events[3][0] == "b" and "Completed" in events[3][1]
    print("PASS  progress callback is called")


if __name__ == "__main__":
    test_orchestrator_runs_phases_in_order()
    test_orchestrator_stops_on_first_error()
    test_orchestrator_preserves_command_error_context()
    test_orchestrator_calls_progress_callback()
    print()
    print("All installer orchestrator tests passed.")
