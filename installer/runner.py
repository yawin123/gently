from __future__ import annotations

import shlex
import subprocess
import threading
import time
import os
import shutil
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class CleanupEntry:
	"""A registered cleanup action to be run when the installation finishes."""
	description: str
	action: Callable[[], None]


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
		# Optional callback(phase_key, line) called immediately after each command.
		self.log_callback: Callable[[str, str], None] | None = None
		# Optional callback(message, yes_key, no_key) -> bool for interactive confirmations.
		# If None, confirm() always returns True (unattended / test mode).
		self.confirm_callback: Callable[[str, str, str], bool] | None = None
		# Stack of cleanup actions (LIFO). Phases push entries after reversible actions
		# (mounts, swap activation, etc.). run_cleanup() drains the stack in reverse order.
		self.cleanup_stack: list[CleanupEntry] = []

	def push_cleanup(self, description: str, action: Callable[[], None]) -> None:
		"""Register a cleanup action to be executed on teardown.

		Actions are run in LIFO order by run_cleanup(), so the last thing registered
		(e.g. a subdirectory mount) is the first to be undone.
		"""
		self.cleanup_stack.append(CleanupEntry(description=description, action=action))

	def pop_cleanup(self) -> CleanupEntry | None:
		"""Remove the top cleanup entry without running it.

		Call this when a phase has already undone the action itself and the
		registered cleanup is no longer needed.
		"""
		return self.cleanup_stack.pop() if self.cleanup_stack else None

	def run_cleanup(self) -> list[tuple[str, Exception]]:
		"""Drain the cleanup stack in LIFO order.

		Each action is called even if a previous one raised; errors are collected
		and returned as a list of (description, exception) pairs. Exceptions are
		also emitted through log_callback if one is set.
		"""
		errors: list[tuple[str, Exception]] = []
		while self.cleanup_stack:
			entry = self.cleanup_stack.pop()
			if self.log_callback:
				self.log_callback("cleanup", f"[cleanup] {entry.description}")
			try:
				entry.action()
			except Exception as exc:
				if self.log_callback:
					self.log_callback("cleanup", f"[cleanup] WARNING: {entry.description} failed: {exc}")
				errors.append((entry.description, exc))
		return errors

	def confirm(self, message: str, yes_key: str = "ui_yes", no_key: str = "ui_no") -> bool:
		"""Ask the user for confirmation.

		In dry-run mode or when no callback is registered, returns True automatically.
		The callback is typically set to backend.show_confirm by the orchestrator.
		"""
		if self.dry_run or self.confirm_callback is None:
			return True
		return self.confirm_callback(message, yes_key, no_key)

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

		phase = spec.phase or ""
		cmd_line = f"$ {' '.join(spec.argv)}"

		if self.dry_run:
			if self.log_callback:
				self.log_callback(phase, f"{cmd_line}  [dry-run]")
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

		# Emit the command line BEFORE executing so the user sees it immediately.
		if self.log_callback:
			self.log_callback(phase, cmd_line)

		# Use streaming execution (line-by-line) when available and a log callback is
		# set, so the user sees output in real time instead of in one batch at the end.
		_stream = getattr(self, "_execute_streaming", None)
		if self.log_callback and _stream is not None:
			result = _stream(spec, lambda line: self.log_callback(phase, f"  {line}"))
		else:
			result = self._execute(spec)
			if self.log_callback and result.stdout.strip():
				for line in result.stdout.strip().splitlines():
					self.log_callback(phase, f"  {line}")
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
		# When there is no input to pipe, use DEVNULL so subprocesses (especially
		# bash -lc) cannot detect a tty on stdin, activate job control, and call
		# tcsetattr while curses owns the terminal.
		if spec.input_text is not None:
			cp = self._run_impl(
				spec.argv,
				cwd=spec.cwd,
				env=spec.env,
				input=spec.input_text,
				capture_output=True,
				text=True,
				check=False,
			)
		else:
			cp = self._run_impl(
				spec.argv,
				cwd=spec.cwd,
				env=spec.env,
				stdin=subprocess.DEVNULL,
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

	def _execute_streaming(
		self,
		spec: CommandSpec,
		line_cb: Callable[[str], None],
	) -> CommandResult:
		"""Execute a command, calling line_cb for each stdout line as it arrives.

		This uses subprocess.Popen so output is streamed in real time instead of
		being captured and emitted in one batch when the process exits.
		"""
		started = time.time()
		stdin_src: int = subprocess.DEVNULL if spec.input_text is None else subprocess.PIPE
		proc = subprocess.Popen(
			spec.argv,
			cwd=spec.cwd,
			env=spec.env,
			stdin=stdin_src,
			stdout=subprocess.PIPE,
			stderr=subprocess.PIPE,
			text=True,
		)
		if spec.input_text is not None and proc.stdin is not None:
			proc.stdin.write(spec.input_text)
			proc.stdin.close()
		stderr_lines: list[str] = []

		def _read_stderr() -> None:
			if proc.stderr is not None:
				for raw in iter(proc.stderr.readline, ""):
					stderr_lines.append(raw.rstrip("\n"))

		stderr_thread = threading.Thread(target=_read_stderr, daemon=True)
		stderr_thread.start()
		stdout_lines: list[str] = []
		if proc.stdout is not None:
			for raw_line in iter(proc.stdout.readline, ""):
				line = raw_line.rstrip("\n")
				stdout_lines.append(line)
				line_cb(line)
		proc.wait()
		stderr_thread.join()
		return CommandResult(
			argv=spec.argv,
			returncode=proc.returncode,
			stdout="\n".join(stdout_lines),
			stderr="\n".join(stderr_lines),
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
		if spec.input_text is not None:
			cp = self._run_impl(
				ssh_cmd,
				env=env,
				input=spec.input_text,
				capture_output=True,
				text=True,
				check=False,
			)
		else:
			cp = self._run_impl(
				ssh_cmd,
				env=env,
				stdin=subprocess.DEVNULL,
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

	def _execute_streaming(
		self,
		spec: CommandSpec,
		line_cb: Callable[[str], None],
	) -> CommandResult:
		"""Execute a remote command via SSH, streaming stdout line-by-line in real time."""
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
		stdin_src: int = subprocess.DEVNULL if spec.input_text is None else subprocess.PIPE
		proc = subprocess.Popen(
			ssh_cmd,
			env=env,
			stdin=stdin_src,
			stdout=subprocess.PIPE,
			stderr=subprocess.PIPE,
			text=True,
		)
		if spec.input_text is not None and proc.stdin is not None:
			proc.stdin.write(spec.input_text)
			proc.stdin.close()
		stderr_lines: list[str] = []

		def _read_stderr() -> None:
			if proc.stderr is not None:
				for raw in iter(proc.stderr.readline, ""):
					stderr_lines.append(raw.rstrip("\n"))

		stderr_thread = threading.Thread(target=_read_stderr, daemon=True)
		stderr_thread.start()
		stdout_lines: list[str] = []
		if proc.stdout is not None:
			for raw_line in iter(proc.stdout.readline, ""):
				line = raw_line.rstrip("\n")
				stdout_lines.append(line)
				line_cb(line)
		proc.wait()
		stderr_thread.join()
		return CommandResult(
			argv=spec.argv,
			returncode=proc.returncode,
			stdout="\n".join(stdout_lines),
			stderr="\n".join(stderr_lines),
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
	from installer.partition import execute as partition_execute
	from installer.stage3 import execute as stage3_execute

	return [
		InstallPhase("preflight", "Preflight", preflight_execute),
		InstallPhase("partition", "Partition", partition_execute),
		InstallPhase("stage3", "Stage3", stage3_execute),
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

	try:
		if backend is not None:
			backend.install_progress_begin([p.key for p in selected])
			runner.log_callback = backend.install_progress_update

		for phase in selected:
			started = time.time()
			if progress_cb:
				progress_cb(phase.key, f"Starting {phase.title}")
			if backend is not None:
				backend.install_progress_update(phase.key, f"Starting {phase.title}")
			try:
				phase.execute(config, runner)
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
			except BaseException as exc:
				# KeyboardInterrupt and similar — still notify the UI before propagating.
				phase_result = InstallPhaseResult(
					key=phase.key,
					title=phase.title,
					status="error",
					duration_sec=time.time() - started,
					error=type(exc).__name__,
				)
				report.phases.append(phase_result)
				if backend is not None:
					backend.install_progress_update(phase.key, f"INTERRUPTED ({type(exc).__name__})")
					try:
						backend.install_progress_end(report)
					except Exception:
						pass
				raise

		if backend is not None:
			backend.install_progress_end(report)

		return report

	finally:
		runner.run_cleanup()


def run_installation_interactive(
	config: Any,
	runner: Runner,
	backend: Any,
	phases: list[InstallPhase] | None = None,
) -> InstallationReport:
	"""Orchestrate installation with an interactive UI backend.

	Prepares the backend, starts run_installation in a background thread,
	then blocks on the calling (main) thread driving the UI.  This keeps
	curses (or any other terminal UI) on the main thread while the
	installation runs in the background.
	"""
	selected = phases if phases is not None else default_install_phases()

	# Wire the backend's confirmation dialog to the runner so phases can ask
	# the user questions (e.g. confirm_wipe) without depending on UIBackend directly.
	runner.confirm_callback = backend.show_confirm

	backend.prepare_install(selected)

	report_box: list[Any] = [None]
	error_box: list[Any] = [None]

	def _run() -> None:
		try:
			report_box[0] = run_installation(config, runner, phases=selected, backend=backend)
		except BaseException as exc:  # noqa: BLE001
			error_box[0] = exc

	install_thread = threading.Thread(target=_run, daemon=True, name="gently-install")
	install_thread.start()

	backend.run_install_ui()  # blocks on the main thread

	install_thread.join(timeout=15.0)

	if error_box[0] is not None:
		raise error_box[0]
	if report_box[0] is None:
		return InstallationReport()
	return report_box[0]
