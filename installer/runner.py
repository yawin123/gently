from __future__ import annotations

import re
import shlex
import subprocess
import threading
import time
import os
import shutil
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, List
import util.log as Log

# Matches ANSI/VT100 escape sequences (colours, cursor movement, etc.).
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]|\x1b[()][AB012]")


def _strip_ansi(text: str) -> str:
    """Remove ANSI terminal escape sequences from *text*."""
    return _ANSI_RE.sub("", text)


class LogCallbackDispatcher:
    """Dispatcher for log callbacks that allows multiple subscribers.
    
    This class enables multiple callbacks to be registered and called
    simultaneously when log events occur. Each callback receives the same
    (phase_key, line) arguments.
    """
    
    def __init__(self) -> None:
        self._callbacks: List[Callable[[str, str], None]] = []
        self._lock = threading.Lock()
    
    def register(self, callback: Callable[[str, str], None]) -> None:
        """Register a callback to be called on log events."""
        with self._lock:
            if callback not in self._callbacks:
                self._callbacks.append(callback)
    
    def unregister(self, callback: Callable[[str, str], None]) -> None:
        """Unregister a previously registered callback."""
        with self._lock:
            if callback in self._callbacks:
                self._callbacks.remove(callback)
    
    def __call__(self, phase_key: str, line: str) -> None:
        """Call all registered callbacks with the given arguments."""
        with self._lock:
            callbacks = self._callbacks.copy()
        for callback in callbacks:
            try:
                callback(phase_key, line)
            except Exception:
                # Log errors but don't let one callback break others
                pass


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


class AbortError(RunnerError):
	"""Raised when the user requests an abort during installation."""


class Runner(ABC):
	def __init__(self, dry_run: bool = False):
		self.dry_run = dry_run
		self.history: list[CommandResult] = []
		# Dispatcher for log callbacks that allows multiple subscribers.
		self.log_dispatcher: LogCallbackDispatcher = LogCallbackDispatcher()
		# Optional callback(message, yes_key, no_key) -> bool for interactive confirmations.
		# If None, confirm() always returns True (unattended / test mode).
		self.confirm_callback: Callable[[str, str, str], bool] | None = None
		# Stack of cleanup actions (LIFO). Phases push entries after reversible actions
		# (mounts, swap activation, etc.). run_cleanup() drains the stack in reverse order.
		self.cleanup_stack: list[CleanupEntry] = []
		# When set, run_shell() wraps all commands with `chroot <path> /bin/bash -lc`.
		# Set by the chroot_prep phase; cleared automatically by run_cleanup().
		self.chroot_path: str | None = None
		# Event that, when set, signals the installation phases to abort cleanly.
		# The installation loop checks this between phases.  Set by the UI thread
		# (e.g. on Esc key) and cleared automatically after the abort is handled.
		self.abort_event: threading.Event | None = None

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
		also emitted through log_dispatcher if one is set.
		"""
		# Clear the abort event unconditionally so cleanup actions can always run —
		# the user already requested the abort and cleanup *must* execute regardless.
		self.abort_event = None
		errors: list[tuple[str, Exception]] = []
		while self.cleanup_stack:
			entry = self.cleanup_stack.pop()
			try:
				entry.action()
			except Exception as exc:
				if self.log_dispatcher:
					self.log_dispatcher("cleanup", f"[cleanup] WARNING: {entry.description} failed: {exc}")
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

	@abstractmethod
	def load_file(self, source_path: str, dest_name: str = None) -> str:
		"""
		Copy a local file to the remote work directory and return its path.

		For SSH transport: copies source_path (local) to work_dir/dest_name (remote).
		For local transport: returns source_path unchanged.

		Args:
			source_path: Local path to the file to copy.
			dest_name: Optional name for the file in the work directory.
			           If None, uses the original filename.

		Returns:
			Absolute path to the file in the work directory.

		Raises:
			FileNotFoundError: If source_path does not exist locally.
		"""
		raise NotImplementedError

	def run(self, spec: CommandSpec) -> CommandResult:
		if not spec.argv:
			raise RunnerError("CommandSpec.argv cannot be empty")

		# Abort check between commands within a phase so the user does not have
		# to wait until the next phase boundary (q/Esc in the curses UI).
		if self.abort_event is not None and self.abort_event.is_set():
			raise AbortError("Installation aborted by user")

		phase = spec.phase or ""
		cmd_line = f"$ {' '.join(spec.argv)}"

		if self.dry_run:
			cmd_line = f"[dry-run] {cmd_line}"

		# Emit the command line BEFORE executing so the user sees it immediately.
		if self.log_dispatcher:
			self.log_dispatcher(phase, cmd_line)

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

		# Use streaming execution (line-by-line) when available and a log callback is
		# set, so the user sees output in real time instead of in one batch at the end.
		_stream = getattr(self, "_execute_streaming", None)
		if self.log_dispatcher and _stream is not None:
			result = _stream(spec, lambda line: self.log_dispatcher(phase, f"  {_strip_ansi(line)}"))
		else:
			result = self._execute(spec)
			if self.log_dispatcher and result.stdout.strip():
				for line in result.stdout.strip().splitlines():
					self.log_dispatcher(phase, f"  {_strip_ansi(line)}")
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
		chroot: bool = False,
	) -> CommandResult:
		"""Run a shell command.

		When chroot=True and chroot_path is set, the command is wrapped with
		`chroot <path> /bin/bash -lc`.  Pass chroot=False (default) for
		operations that must run on the host even when chroot_path is active
		(e.g. mount, umount).
		"""
		if chroot and self.chroot_path:
			argv = ["chroot", self.chroot_path, "/bin/bash", "-lc", command]
		else:
			argv = ["bash", "-lc", command]
		return self.run(
			CommandSpec(
				argv=argv,
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

	def load_file(self, source_path: str, dest_name: str = None) -> str:
		"""
		Verify a local file exists and return its absolute path.

		For LocalRunner, the file is already local, so no copy is needed.
		The dest_name parameter is ignored (kept for interface consistency).

		Args:
			source_path: Local path to the file.
			dest_name: Ignored for local runner.

		Returns:
			Absolute path to the file.

		Raises:
			FileNotFoundError: If source_path does not exist.
		"""
		path = os.path.abspath(source_path)
		if not os.path.exists(path):
			raise FileNotFoundError(f"File not found: {source_path}")
		return path

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
				line_cb(_strip_ansi(line))
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
		# Cache de archivos cargados: {remote_path: local_fingerprint}
		self._loaded_files: dict[str, str] = {}

	@property
	def transport(self) -> str:
		return f"ssh:{self.target}"

	def _compute_file_hash(self, filepath: str) -> str:
		"""Compute SHA256 hash of a local file using sha256sum."""
		result = subprocess.run(
			["sha256sum", filepath],
			capture_output=True,
			text=True,
			check=True,
		)
		return result.stdout.split()[0]

	def _get_remote_file_hash(self, remote_path: str) -> str | None:
		"""Get SHA256 hash of a remote file via ssh."""
		try:
			result = self.run_shell(
				f"sha256sum {shlex.quote(remote_path)}",
				phase=None,
				check=False,
			)
			if result.returncode == 0:
				# Output format: "hash  filename"
				return result.stdout.strip().split()[0]
		except Exception:
			pass
		return None

	def load_file(self, source_path: str, dest_path: str = None) -> str:
		"""
		Copy a local file to the remote work directory and return its path.

		For SSH transport: copies source_path (local) to dest_path (remote).
		Updates the remote work directory if it doesn't exist.
		Skips copy if file already exists with same content (based on SHA256 hash).

		Args:
			source_path: Local path to the file.
			dest_path: Remote path where the file should be copied.
			           If a directory, the file is copied with its original name.
			           If None, defaults to /tmp/<filename>.

		Returns:
			Absolute path to the file in the remote work directory.

		Raises:
			FileNotFoundError: If source_path does not exist.
		"""
		path = os.path.abspath(source_path)
		filename = os.path.basename(path)

		if dest_path:
			# If dest_path is a directory, use it with the original filename
			if os.path.isdir(dest_path):
				remote_path = os.path.join(dest_path, filename)
			else:
				remote_path = dest_path
		else:
			# No dest_path specified, use /tmp/<filename>
			remote_path = f"/tmp/{filename}"

		remote_dir = os.path.dirname(remote_path)

		if self.dry_run:
			# In dry-run mode, simulate the copy and return the remote path
			return remote_path
		
		if not os.path.exists(path):
			raise FileNotFoundError(f"File not found: {source_path}")

		# Check if file is already loaded with same content
		if remote_path in self._loaded_files:
			local_hash = self._compute_file_hash(path)
			if self._loaded_files[remote_path] == local_hash:
				# File already exists with same content, skip copy
				return remote_path

		# Compute local file hash
		local_hash = self._compute_file_hash(path)

		# Check if remote file exists and has same hash
		remote_hash = self._get_remote_file_hash(remote_path)
		if remote_hash and remote_hash == local_hash:
			# Remote file exists with same content, skip copy
			self._loaded_files[remote_path] = local_hash
			return remote_path

		# Ensure the remote work directory exists
		self.run_shell(
			f"mkdir -p {shlex.quote(remote_dir)}",
			phase=None,
		)

		# Copy the file using scp
		scp_cmd = [
			"scp", *self._ssh_opts(),
			path, f"{self.target}:{remote_path}"
		]
		scp_cmd, scp_env = self._with_auth(scp_cmd)

		cp = subprocess.run(
			scp_cmd,
			env=scp_env,
			capture_output=True,
			text=True,
			check=False,
		)

		if cp.returncode != 0:
			raise RunnerError(
				f"Failed to copy {source_path} to {self.target}:{remote_path}: {cp.stderr.strip()}"
			)

		# Update cache
		self._loaded_files[remote_path] = local_hash

		return remote_path

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
				line_cb(_strip_ansi(line))
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
	from installer.chroot import execute as chroot_prep_execute
	from installer.portage import execute as portage_execute
	from installer.kernel import execute as kernel_execute

	return [
		InstallPhase("preflight",   "Preflight",        preflight_execute),
		InstallPhase("partition",   "Partition",        partition_execute),
		InstallPhase("stage3",      "Stage3",           stage3_execute),
		InstallPhase("chroot_prep", "Chroot Prep",      chroot_prep_execute),
		InstallPhase("portage",     "Portage",          portage_execute),
		InstallPhase("kernel",      "Kernel",           kernel_execute),
		InstallPhase("system",      "System",           _placeholder),
		InstallPhase("services",    "Services",         _placeholder),
		InstallPhase("users",       "Users",            _placeholder),
		InstallPhase("bootloader",  "Bootloader",       _placeholder),
		InstallPhase("packages",    "Packages",         _placeholder),
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

	if isinstance(runner.log_dispatcher, LogCallbackDispatcher):
		runner.log_dispatcher.register(Log._write)

	try:
		if backend is not None:
			backend.install_progress_begin([p.key for p in selected])
			# Register the backend callback with the log dispatcher instead of replacing it.
			# This allows multiple callbacks (e.g., file logging + UI updates) to receive log events.
			if isinstance(runner.log_dispatcher, LogCallbackDispatcher):
				runner.log_dispatcher.register(backend.install_progress_update)
			# In standalone mode (when prepare_install was not called),
			# wire the abort event so the UI's q/Esc reaches the runner.
			if runner.abort_event is None:
				runner.abort_event = getattr(backend, '_abort_event', None)

		for phase in selected:
			# Check for abort request between phases.
			if runner.abort_event is not None and runner.abort_event.is_set():
				raise AbortError("Installation aborted by user")

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
			except AbortError:
				# User-requested abort — still capture the phase result but stop
				# processing further phases.  Cleanup will run in the finally block.
				phase_result = InstallPhaseResult(
					key=phase.key,
					title=phase.title,
					status="error",
					duration_sec=time.time() - started,
					error="Aborted by user",
				)
				report.phases.append(phase_result)
				if backend is not None:
					backend.install_progress_update(phase.key, "ABORTED by user")
				raise
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
				raise

		return report

	finally:
		runner.run_cleanup()
		if backend is not None:
			try:
				backend.install_progress_end(report)
			except Exception:
				pass
		runner.abort_event = None


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
	runner.abort_event = threading.Event()
	# Let the UI abort (Q/Esc) signal the same event the runner checks.
	backend._abort_event = runner.abort_event

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
