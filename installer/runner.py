from __future__ import annotations

import shlex
import subprocess
import time
import os
import shutil
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class CommandSpec:
	argv: list[str]
	check: bool = True
	cwd: str | None = None
	env: dict[str, str] | None = None
	input_text: str | None = None
	phase: str | None = None
	description: str | None = None


@dataclass
class CommandResult:
	argv: list[str]
	returncode: int
	stdout: str
	stderr: str
	duration_sec: float
	transport: str
	skipped: bool = False
	phase: str | None = None


class RunnerError(Exception):
	pass


class CommandExecutionError(RunnerError):
	def __init__(self, spec: CommandSpec, result: CommandResult):
		self.spec = spec
		self.result = result
		phase = f"[{spec.phase}] " if spec.phase else ""
		cmd = shlex.join(spec.argv)
		super().__init__(
			f"{phase}Command failed (rc={result.returncode}): {cmd}\n{result.stderr.strip()}"
		)


class Runner(ABC):
	def __init__(self, dry_run: bool = False):
		self.dry_run = dry_run
		self.history: list[CommandResult] = []

	@property
	@abstractmethod
	def transport(self) -> str:
		raise NotImplementedError

	@abstractmethod
	def _execute(self, spec: CommandSpec) -> CommandResult:
		raise NotImplementedError

	def run(self, spec: CommandSpec) -> CommandResult:
		if not spec.argv:
			raise RunnerError("CommandSpec.argv cannot be empty")

		if self.dry_run:
			result = CommandResult(
				argv=spec.argv,
				returncode=0,
				stdout="",
				stderr="",
				duration_sec=0.0,
				transport=self.transport,
				skipped=True,
				phase=spec.phase,
			)
			self.history.append(result)
			return result

		result = self._execute(spec)
		self.history.append(result)
		if spec.check and result.returncode != 0:
			raise CommandExecutionError(spec, result)
		return result

	def run_many(self, specs: list[CommandSpec]) -> list[CommandResult]:
		return [self.run(spec) for spec in specs]

	def run_shell(
		self,
		command: str,
		check: bool = True,
		cwd: str | None = None,
		env: dict[str, str] | None = None,
		phase: str | None = None,
	) -> CommandResult:
		return self.run(
			CommandSpec(
				argv=["bash", "-lc", command],
				check=check,
				cwd=cwd,
				env=env,
				phase=phase,
			)
		)


class LocalRunner(Runner):
	def __init__(
		self,
		dry_run: bool = False,
		run_impl: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
	):
		super().__init__(dry_run=dry_run)
		self._run_impl = run_impl

	@property
	def transport(self) -> str:
		return "local"

	def _execute(self, spec: CommandSpec) -> CommandResult:
		started = time.time()
		cp = self._run_impl(
			spec.argv,
			cwd=spec.cwd,
			env=spec.env,
			input=spec.input_text,
			capture_output=True,
			text=True,
			check=False,
		)
		return CommandResult(
			argv=spec.argv,
			returncode=cp.returncode,
			stdout=cp.stdout,
			stderr=cp.stderr,
			duration_sec=time.time() - started,
			transport=self.transport,
			phase=spec.phase,
		)


class SshRunner(Runner):
	def __init__(
		self,
		target: str,
		dry_run: bool = False,
		port: int | None = None,
		identity_file: str | None = None,
		password: str | None = None,
		control_path: str = "/tmp/gently-ssh-%r@%h:%p",
		verify_host_key: bool = False,
		extra_ssh_options: list[str] | None = None,
		run_impl: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
	):
		super().__init__(dry_run=dry_run)
		self.target = target
		self.port = port
		self.identity_file = identity_file
		self.password = password
		self.control_path = control_path
		self.verify_host_key = verify_host_key
		self.extra_ssh_options = list(extra_ssh_options or [])
		self._run_impl = run_impl
		self._session_started = False

	@property
	def transport(self) -> str:
		return f"ssh:{self.target}"

	def _ssh_opts(self) -> list[str]:
		opts = [
			"-o", "BatchMode=no",
			"-o", "ControlMaster=auto",
			"-o", "ControlPersist=600",
			"-o", f"ControlPath={self.control_path}",
		]
		if not self.verify_host_key:
			opts += [
				"-o", "StrictHostKeyChecking=no",
				"-o", "UserKnownHostsFile=/dev/null",
				"-o", "GlobalKnownHostsFile=/dev/null",
			]
		if self.port is not None:
			opts += ["-p", str(self.port)]
		if self.identity_file:
			opts += ["-i", self.identity_file]
		opts += self.extra_ssh_options
		return opts

	def _with_auth(self, base_cmd: list[str]) -> tuple[list[str], dict[str, str] | None]:
		if self.password is None:
			return base_cmd, None
		if shutil.which("sshpass") is None:
			raise RunnerError(
				"SSH password mode requires 'sshpass', which is not installed. "
				"Use SSH keys or install sshpass in the execution environment."
			)
		env = os.environ.copy()
		env["SSHPASS"] = self.password
		return ["sshpass", "-e", *base_cmd], env

	def ensure_session(self) -> None:
		if self._session_started or self.dry_run:
			return
		cmd, env = self._with_auth(["ssh", *self._ssh_opts(), "-MNf", self.target])
		cp = self._run_impl(cmd, env=env, capture_output=True, text=True, check=False)
		if cp.returncode != 0:
			raise RunnerError(
				f"Failed to open SSH control session to {self.target}: {cp.stderr.strip()}"
			)
		self._session_started = True

	def close(self) -> None:
		if not self._session_started or self.dry_run:
			return
		cmd, env = self._with_auth(["ssh", *self._ssh_opts(), "-O", "exit", self.target])
		self._run_impl(cmd, env=env, capture_output=True, text=True, check=False)
		self._session_started = False

	def __enter__(self) -> SshRunner:
		self.ensure_session()
		return self

	def __exit__(self, exc_type, exc, tb) -> None:
		self.close()

	def _execute(self, spec: CommandSpec) -> CommandResult:
		self.ensure_session()

		prelude: list[str] = []
		if spec.cwd:
			prelude.append(f"cd {shlex.quote(spec.cwd)}")
		if spec.env:
			env_assign = " ".join(
				f"{k}={shlex.quote(v)}" for k, v in spec.env.items()
			)
			prelude.append(f"export {env_assign}")

		core = shlex.join(spec.argv)
		remote_cmd = core if not prelude else " && ".join([*prelude, core])
		ssh_cmd = [
			"ssh", *self._ssh_opts(), self.target,
			remote_cmd,
		]
		ssh_cmd, env = self._with_auth(ssh_cmd)

		started = time.time()
		cp = self._run_impl(
			ssh_cmd,
			env=env,
			input=spec.input_text,
			capture_output=True,
			text=True,
			check=False,
		)
		return CommandResult(
			argv=spec.argv,
			returncode=cp.returncode,
			stdout=cp.stdout,
			stderr=cp.stderr,
			duration_sec=time.time() - started,
			transport=self.transport,
			phase=spec.phase,
		)


def build_runner(
	target: str,
	dry_run: bool = False,
	port: int | None = None,
	identity_file: str | None = None,
	ssh_password: str | None = None,
) -> Runner:
	if target == "local":
		return LocalRunner(dry_run=dry_run)
	if target.startswith("ssh:"):
		return SshRunner(
			target=target[4:],
			dry_run=dry_run,
			port=port,
			identity_file=identity_file,
			password=ssh_password,
		)
	raise RunnerError(
		f"Unknown target {target!r}. Expected 'local' or 'ssh:user@host'."
	)


@dataclass
class InstallPhase:
	key: str
	title: str
	execute: Callable[[Any, Runner], None]


@dataclass
class InstallPhaseResult:
	key: str
	title: str
	status: str  # "ok" | "error"
	duration_sec: float
	error: str | None = None


@dataclass
class InstallationReport:
	phases: list[InstallPhaseResult] = field(default_factory=list)

	@property
	def ok(self) -> bool:
		return all(p.status == "ok" for p in self.phases)


class InstallPhaseError(RunnerError):
	def __init__(
		self,
		phase_key: str,
		phase_title: str,
		cause: Exception,
		partial_report: InstallationReport,
	):
		self.phase_key = phase_key
		self.phase_title = phase_title
		self.cause = cause
		self.partial_report = partial_report
		super().__init__(f"Install phase '{phase_key}' failed: {cause}")


def _placeholder(_config: Any, _runner: Runner) -> None:
	return None


def default_install_phases() -> list[InstallPhase]:
	from installer.preflight import execute as preflight_execute

	return [
		InstallPhase("preflight", "Preflight", preflight_execute),
		InstallPhase("partition", "Partition", _placeholder),
		InstallPhase("stage3", "Stage3", _placeholder),
		InstallPhase("portage", "Portage", _placeholder),
		InstallPhase("kernel", "Kernel", _placeholder),
		InstallPhase("system", "System", _placeholder),
		InstallPhase("services", "Services", _placeholder),
		InstallPhase("users", "Users", _placeholder),
		InstallPhase("bootloader", "Bootloader", _placeholder),
		InstallPhase("packages", "Packages", _placeholder),
	]


def run_installation(
	config: Any,
	runner: Runner,
	phases: list[InstallPhase] | None = None,
	progress_cb: Callable[[str, str], None] | None = None,
	backend: Any = None,
) -> InstallationReport:
	selected = phases if phases is not None else default_install_phases()
	report = InstallationReport()

	if backend is not None:
		backend.install_progress_begin([p.key for p in selected])

	for phase in selected:
		started = time.time()
		if progress_cb:
			progress_cb(phase.key, f"Starting {phase.title}")
		if backend is not None:
			backend.install_progress_update(phase.key, f"Starting {phase.title}")
		try:
			phase.execute(config, runner)
			# Log every command that was executed during this phase.
			if backend is not None:
				for cmd_result in runner.history:
					if cmd_result.phase == phase.key:
						backend.install_progress_update(
							phase.key,
							f"$ {' '.join(cmd_result.argv)}{'  [dry-run]' if cmd_result.skipped else ''}",
						)
						if not cmd_result.skipped and cmd_result.stdout.strip():
							for out_line in cmd_result.stdout.strip().splitlines():
								backend.install_progress_update(phase.key, f"  {out_line}")
			phase_result = InstallPhaseResult(
				key=phase.key,
				title=phase.title,
				status="ok",
				duration_sec=time.time() - started,
			)
			report.phases.append(phase_result)
			if progress_cb:
				progress_cb(phase.key, f"Completed {phase.title}")
			if backend is not None:
				backend.install_progress_update(phase.key, f"Completed {phase.title}")
		except Exception as exc:
			phase_result = InstallPhaseResult(
				key=phase.key,
				title=phase.title,
				status="error",
				duration_sec=time.time() - started,
				error=str(exc),
			)
			report.phases.append(phase_result)
			if progress_cb:
				progress_cb(phase.key, f"Failed {phase.title}: {exc}")
			if backend is not None:
				backend.install_progress_update(phase.key, f"FAILED: {exc}")
				try:
					backend.install_progress_end(report)
				except Exception:
					pass
			raise InstallPhaseError(
				phase_key=phase.key,
				phase_title=phase.title,
				cause=exc,
				partial_report=report,
			) from exc

	if backend is not None:
		backend.install_progress_end(report)

	return report
