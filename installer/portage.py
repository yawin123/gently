"""Portage phase — configure Portage and sync the tree.

Assumes chroot_prep has already run (runner.chroot_path is set to MOUNTPOINT).
"""
from __future__ import annotations

import shlex

from installer.runner import CommandSpec, Runner, RunnerError
from model.config import GentlyConfig

PHASE_KEY  = "portage"
MOUNTPOINT = "/mnt/gentoo"
DISTCC_DEFAULT_PORT = 3632


class PortageError(RunnerError):
    pass


# ---------------------------------------------------------------------------
# Profile selection
# ---------------------------------------------------------------------------

def _apply_profile(config: GentlyConfig, runner: Runner) -> None:
	"""Apply the explicit profile from config, validating it exists first.

	If portage.profile.name is absent the stage3's built-in default is left
	untouched — it is already aligned with the chosen variant.
	Must be called after emerge-webrsync so the profile tree is available.

	In dry-run the validation is skipped so the command list remains
	realistic (the tree hasn't been synced, so eselect would appear empty).
	"""
	if not (config.portage and config.portage.profile and config.portage.profile.name):
		return

	if runner.dry_run:
		runner.run_shell(
			f"eselect profile set {shlex.quote(config.portage.profile.name)}",
			phase=PHASE_KEY,
			chroot=True,
		)
		return

	profile = config.portage.profile.name
	result = runner.run_shell(
		"eselect profile list",
		phase=PHASE_KEY,
		chroot=True,
		check=False,
	)
	if profile not in result.stdout:
		raise PortageError(
			f"Profile '{profile}' not found in the available profile list. "
			"Run 'eselect profile list' inside the chroot to see valid options."
		)
	runner.run_shell(
		f"eselect profile set {shlex.quote(profile)}",
		phase=PHASE_KEY,
		chroot=True,
	)


def _setup_portage(config: GentlyConfig, runner: Runner) -> None:
	# Configure timezone before locale-gen (handbook order).
	# Use a relative symlink so it works correctly with alternate ROOT environments.
	if config.system and config.system.timezone:
		timezone = config.system.timezone
		zoneinfo_rel = f"../usr/share/zoneinfo/{timezone}"
		runner.run_shell(
			f"ln -sf {shlex.quote(zoneinfo_rel)} /etc/localtime",
			phase=PHASE_KEY,
			chroot=True,
		)

	# Preconfigure locale.gen with user's locale to avoid generating 500+ locales
	if config.system and config.system.locale:
		locale_entry = f"{config.system.locale} UTF-8"
		runner.run_shell(
			f"echo '{locale_entry}' > /etc/locale.gen",
			phase=PHASE_KEY,
			chroot=True
		)
		runner.run_shell("locale-gen", phase=PHASE_KEY, chroot=True)
	else:
		runner.run_shell("locale-gen", phase=PHASE_KEY, chroot=True)
	
	# Create Portage configuration directories
	runner.run_shell("mkdir -p /var/db/repos/gentoo", phase=PHASE_KEY, chroot=True)
	runner.run_shell("chown -R portage:portage /var/db/repos/gentoo", phase=PHASE_KEY, chroot=True)
	runner.run_shell("mkdir -p /etc/portage/package.accept_keywords", phase=PHASE_KEY, chroot=True)
	runner.run_shell("mkdir -p /etc/portage/package.license", phase=PHASE_KEY, chroot=True)
	
	# Synchronize the Portage tree
	runner.run_shell("emerge-webrsync", phase=PHASE_KEY, chroot=True)

	# Apply explicit profile from config (validates against eselect list first).
	# Must run after emerge-webrsync so the profile tree is available.
	_apply_profile(config, runner)

def _parse_distcc_host(spec: str) -> str:
	"""Extract hostname/IP from a distcc host spec, stripping /N and ,options."""
	base = spec.split("/", 1)[0]
	base = base.split(",", 1)[0]
	return base.strip()


def _get_nproc(runner: Runner) -> int:
	"""Get number of available CPUs using the runner."""
	result = runner.run(
		CommandSpec(
			argv=["nproc"],
			check=True,
			phase=PHASE_KEY,
		),
	)
	try:
		return int(result.stdout.strip())
	except (ValueError, AttributeError):
		return 1


def _get_total_memory_mb(runner: Runner) -> int:
	"""Get total system RAM + swap in MB using the runner.

	Reading both because swap supplements RAM for gcc's virtual-memory
	usage even though physical pages are what trigger the OOM killer.
	"""
	result = runner.run_shell(
		r"awk '/MemTotal/ {ram=$2} /SwapTotal/ {swap=$2} END {printf \"%d\", (ram+swap)/1024}' /proc/meminfo",
		check=True,
		phase=PHASE_KEY,
	)
	try:
		return int(result.stdout.strip())
	except (ValueError, AttributeError):
		return 1024  # safe fallback


def _ram_aware_jobs(ram_mb: int, cpu_jobs: int) -> int:
	"""Limit parallel jobs based on available RAM.

	Each gcc process can consume significant memory, especially under
	multilib (compiles twice).  Conservative heuristic:
	  - < 2 GB   →  1 job  (OOM-safe for constrained VMs/containers)
	  - 2–3.9 GB →  2 jobs
	  - 4–7.9 GB → min(4, cpu_jobs)
	  - 8 GB+    → cpu_jobs (no RAM bottleneck)
	"""
	if ram_mb < 2048:
		return 1
	if ram_mb < 4096:
		return min(2, cpu_jobs)
	if ram_mb < 8192:
		return min(4, cpu_jobs)
	return cpu_jobs


def _calculate_makeopts(config: GentlyConfig, runner: Runner) -> str:
	"""Calculate MAKEOPTS based on distcc, available CPUs, and RAM.

	Respects config.portage.makeopts if explicitly set.

	Semantics:
	  -j N  — max parallel jobs to spawn (distcc or local).
	  -l N  — load cap: don't spawn new jobs if load average ≥ N.

	With distcc the remote workers handle compilation so -j can be
	aggressive (nproc × 3), while -l keeps the local CPU from drowning
	in preprocessor/linker tasks.

	Without distcc, -j is RAM-constrained (prevents OOM on low-memory
	systems) and -l{nproc} prevents oversubscription under link-heavy
	or I/O-bound loads.
	"""
	# 1. User-specified makeopts has priority
	if config.portage and config.portage.makeopts:
		return config.portage.makeopts
	
	# 2. Discover CPU count
	nproc = _get_nproc(runner)
	
	d = config.distcc
	if d and d.enabled:
		# Distcc: remote workers do the heavy lifting.
		# -j is NOT RAM-limited — remote machines have their own RAM.
		# -l{nproc} prevents local preprocessor/linker oversubscription.
		if d.makeopts_jobs:
			jobs = d.makeopts_jobs
		else:
			jobs = nproc * 3
		return f"-j{jobs} -l{nproc}"

	# 3. Local build: RAM-aware -j, plus -l{nproc} for load control.
	ram_mb = _get_total_memory_mb(runner)
	cpu_jobs = nproc + 1
	jobs = max(1, _ram_aware_jobs(ram_mb, cpu_jobs))
	return f"-j{jobs} -l{nproc}"


def _is_inside_vm(runner: Runner) -> bool:
	"""Detect if running inside a virtual machine.

	Checks /sys/class/dmi/id/product_name and product_version for common VM
	signatures. Returns True if a VM is detected.
	"""
	result = runner.run_shell(
		"cat /sys/class/dmi/id/product_name /sys/class/dmi/id/product_version 2>/dev/null",
		check=False,
		phase=PHASE_KEY,
		chroot=True,
	)
	product = (result.stdout + result.stderr).lower()
	vm_signatures = ["virtualbox", "qemu", "kvm", "vmware", "microsoft", "parallels", "xen", "vm"]
	return any(sig in product for sig in vm_signatures)


def _discover_march(runner: Runner) -> str:
	"""Discover the CPU microarchitecture for -march.

	Runs `gcc -march=native -Q --help=target` INSIDE THE CHROOT to find
	what -march=native resolves to on the target machine.

	Returns the architecture (e.g., 'skylake') or 'native' as fallback.
	For VMs, uses a more conservative 'x86-64' baseline to avoid
	compiling instructions that the VM may not fully support.
	"""
	# For VMs, use a conservative baseline to avoid SIGILL from unsupported
	# instructions that the host CPU may have but the VM doesn't expose.
	if _is_inside_vm(runner):
		return "x86-64"

	result = runner.run_shell(
		"gcc -march=native -Q --help=target 2>/dev/null",
		check=False,
		phase=PHASE_KEY,
		chroot=True,
	)
	for line in result.stdout.splitlines():
		line = line.strip()
		if line.startswith("-march="):
			parts = line.split(None, 1)  # Split on whitespace: ["-march=", "skylake"]
			if len(parts) >= 2:
				march = parts[1].strip()
				if march and march not in ("[enabled]", "[disabled]", "native"):
					return march
	# Couldn't determine specific architecture — fall back to native
	return "native"


def _calculate_cflags(config: GentlyConfig, distcc_enabled: bool, runner: Runner) -> str:
	"""Calculate CFLAGS based on configuration and distcc status.

	If user specified CFLAGS in config, use those as base. If they don't
	already contain -march, auto-discover and append it.
	Otherwise use sensible defaults with auto-discovered -march.
	"""
	user_cflags = config.portage and config.portage.cflags

	if user_cflags:
		base = user_cflags
	else:
		base = "-O2 -pipe"

	# Auto-discover -march if not already set by the user
	if "-march=" not in base:
		if distcc_enabled:
			march = _discover_march(runner)
		else:
			march = "native"
		base += f" -march={march}"

	return base


def _calculate_cxxflags(config: GentlyConfig, cflags: str) -> str:
	"""Calculate CXXFLAGS based on configuration.

	If user specified custom CXXFLAGS, use those.
	Otherwise, inherit from CFLAGS.
	"""
	if config.portage and config.portage.cxxflags:
		return config.portage.cxxflags
	
	# Default: inherit from CFLAGS
	return cflags


def _write_makeconf(config: GentlyConfig, runner: Runner) -> None:
	"""Write /etc/portage/make.conf following the Gentoo stage3 template style.

	Uses COMMON_FLAGS idiom (the standard Gentoo pattern).
	Does NOT include:
	- CHOST (auto-detected by Gentoo)
	- ACCEPT_KEYWORDS (belongs in /etc/portage/package.accept_keywords/)
	- ACCEPT_LICENSE (belongs in /etc/portage/package.license/)
	"""
	make_config_path = "/etc/portage/make.conf"

	# Check if distcc is enabled
	distcc_enabled = config.distcc is not None and config.distcc.enabled
	
	# Calculate compiler flags
	common_flags = _calculate_cflags(config, distcc_enabled, runner)
	cxxflags = _calculate_cxxflags(config, common_flags)
	
	# Calculate MAKEOPTS
	makeopts = _calculate_makeopts(config, runner)
	
	# Build make.conf content in the Gentoo style
	lines = [
		"# These settings were generated by Gently installer",
		"# Please consult /usr/share/portage/config/make.conf.example for a more",
		"# detailed example.",
		"",
		f'COMMON_FLAGS="{common_flags}"',
		'CFLAGS="${COMMON_FLAGS}"',
		f'CXXFLAGS="{cxxflags}"' if cxxflags != common_flags else 'CXXFLAGS="${COMMON_FLAGS}"',
		"",
		f'MAKEOPTS="{makeopts}"',
	]
	
	# Add USE flags if specified
	if config.portage and config.portage.use:
		use_str = " ".join(config.portage.use)
		lines.append(f'USE="{use_str}"')
	
	# Add FEATURES if specified
	if config.portage and config.portage.features:
		features_str = " ".join(config.portage.features)
		lines.append(f'FEATURES="{features_str}"')
	
	# Add VIDEO_CARDS if specified
	if config.portage and config.portage.video_cards:
		video_str = " ".join(config.portage.video_cards)
		lines.append(f'VIDEO_CARDS="{video_str}"')
	
	# Add INPUT_DEVICES if specified
	if config.portage and config.portage.input_devices:
		input_str = " ".join(config.portage.input_devices)
		lines.append(f'INPUT_DEVICES="{input_str}"')

	# Add GENTOO_MIRRORS if specified
	if config.portage and config.portage.mirrors:
		mirrors_str = " ".join(config.portage.mirrors)
		lines.append(f'GENTOO_MIRRORS="{mirrors_str}"')

	# Binary package host (binpkg)
	# Binary repo entries go to binrepos.conf via _setup_binrepos(), not here.
	if config.portage and config.portage.binpkg_format:
		lines.append(f'BINPKG_FORMAT="{config.portage.binpkg_format}"')

	# Inject extra FEATURES tokens (getbinpkg, distcc).
	# We locate the existing FEATURES= line if present and append to it,
	# or create a new one.
	extra_features: list[str] = []
	if config.portage and config.portage.getbinpkg:
		extra_features.append("getbinpkg")
	if distcc_enabled and config.distcc:
		d = config.distcc
		if d.hosts:
			hosts_str = " ".join(d.hosts)
			lines.append(f'DISTCC_HOSTS="{hosts_str}"')
		extra_features.append("distcc")
		extra_features.append("-network-sandbox")
	if extra_features:
		features_line = next((i for i, l in enumerate(lines) if l.startswith("FEATURES=")), None)
		token_str = " ".join(extra_features)
		if features_line is not None:
			lines[features_line] = lines[features_line].rstrip('"') + f' {token_str}"'
		else:
			lines.append(f'FEATURES="{token_str}"')

	# Write the make.conf file — first line overwrites, rest append
	for i, line in enumerate(lines):
		op = ">" if i == 0 else ">>"
		runner.run_shell(
			f"echo {shlex.quote(line)} {op} {make_config_path}",
			phase=PHASE_KEY,
			chroot=True,
		)


def _setup_distcc(config: GentlyConfig, runner: Runner) -> None:
	"""Configure distcc inside the chroot.

	1. Emerges sys-devel/distcc if distcc is enabled.
	2. Validates the host list is not empty.
	3. Checks TCP connectivity to each host (warning only — non-blocking).
	4. Writes distcc hosts configuration via distcc-config inside the chroot.

	Does nothing if distcc is not enabled or has no hosts configured.
	"""
	d = config.distcc
	if d is None or not d.enabled:
		return

	# 1. Install distcc inside the chroot.
	# binutils-dev provides libiberty.h required by distcc
	# Install binutils-dev first (needed for libiberty.h header)
	#runner.run_shell("emerge --oneshot sys-devel/binutils-dev", phase=PHASE_KEY, chroot=True)
	# Now install distcc (no deps needed since binutils-dev is installed)
	runner.run_shell("emerge --oneshot sys-devel/distcc", phase=PHASE_KEY, chroot=True)

	# 2. Validate hosts list.
	hosts = list(d.hosts or [])
	if not hosts:
		raise PortageError("distcc.enabled=true but distcc.hosts is empty")

	# 3. Check TCP connectivity (warning only — non-blocking).
	port = d.port or DISTCC_DEFAULT_PORT
	for host_spec in hosts:
		host = _parse_distcc_host(host_spec)
		if not host:
			raise PortageError(f"Invalid distcc host entry: {host_spec!r}")
		runner.run(
			CommandSpec(
				argv=[
					"python3",
					"-c",
					(
						"import socket,sys; "
						"socket.create_connection((sys.argv[1], int(sys.argv[2])), 2).close()"
					),
					host,
					str(port),
				],
				check=False,  # Warning only — host may come online later.
				phase=PHASE_KEY,
			)
		)

	# 4. Write the hosts file inside the chroot.
	if d.pump_mode:
		hosts_str = " ".join(f"++{h}" for h in d.hosts)
	else:
		hosts_str = " ".join(d.hosts)

	runner.run_shell(
		f"/usr/bin/distcc-config --set-hosts {shlex.quote(hosts_str)}",
		phase=PHASE_KEY,
		chroot=True
	)


# ---------------------------------------------------------------------------
# Phase entry point
# ---------------------------------------------------------------------------

def _setup_cpu_flags(config: GentlyConfig, runner: Runner) -> None:
	"""Write CPU_FLAGS_* into /etc/portage/package.use/00cpu-flags.

	If config.portage.cpu_flags is set, use those values directly.
	Otherwise, emerge cpuid2cpuflags and auto-detect them from the running CPU.
	"""
	runner.run_shell(
		"mkdir -p /etc/portage/package.use",
		phase=PHASE_KEY,
		chroot=True,
	)

	if config.portage and config.portage.cpu_flags:
		flags_str = " ".join(config.portage.cpu_flags)
		runner.run_shell(
			f"echo {shlex.quote(f'*/* {flags_str}')} > /etc/portage/package.use/00cpu-flags",
			phase=PHASE_KEY,
			chroot=True,
		)
	else:
		# Auto-detect: emerge the tool, run it, write the result.
		runner.run_shell(
			"emerge --oneshot app-portage/cpuid2cpuflags",
			phase=PHASE_KEY,
			chroot=True,
		)
		runner.run_shell(
			'echo "*/* $(cpuid2cpuflags)" > /etc/portage/package.use/00cpu-flags',
			phase=PHASE_KEY,
			chroot=True,
		)


def _write_package_config(config: GentlyConfig, runner: Runner) -> None:
	"""Write /etc/portage/package.* configuration files.

	- package.accept_keywords/gently if accept_keywords set
	- package.license/gently if accept_license set
	"""
	if not config.portage:
		return
	
	# Ensure directories exist
	runner.run_shell(
		"mkdir -p /etc/portage/package.accept_keywords /etc/portage/package.license",
		phase=PHASE_KEY,
		chroot=True,
	)
	
	# ACCEPT_KEYWORDS → /etc/portage/package.accept_keywords/gently
	if config.portage.accept_keywords:
		runner.run_shell(
			f"echo '*/* {shlex.quote(config.portage.accept_keywords)}' "
			f"> /etc/portage/package.accept_keywords/gently",
			phase=PHASE_KEY,
			chroot=True,
		)
	
	# ACCEPT_LICENSE → /etc/portage/package.license/gently
	if config.portage.accept_license:
		runner.run_shell(
			f"echo '*/* {shlex.quote(config.portage.accept_license)}' "
			f"> /etc/portage/package.license/gently",
			phase=PHASE_KEY,
			chroot=True,
		)
	
	runner.run_shell(
		f"cat /etc/portage/make.conf",
		phase=PHASE_KEY,
		chroot=True,
	)

	# Update @world to apply new configuration (e.g., accept_keywords)
	runner.run_shell(
		f"emerge --verbose --update --deep --changed-use @world",
		phase=PHASE_KEY,
		chroot=True,
	)

	# Clean up unnecessary packages after the world update
	runner.run_shell(
		f"emerge --depclean",
		phase=PHASE_KEY,
		chroot=True,
	)


def _binrepos_sync_uri(config: GentlyConfig, repo) -> str:
	"""Construct sync-uri from stage3 mirror + arch + profile version + subarch.

	The URL pattern used by official Gentoo mirrors is:
	  {mirror}/releases/{arch}/binpackages/{profile_ver}/{subarch}/

	The profile version (e.g. "23.0") is extracted from portage.profile.name
	if present; otherwise "23.0" is used as a sensible default.
	"""
	mirror  = (config.stage3 and config.stage3.mirror) or "https://distfiles.gentoo.org"
	arch    = (config.stage3 and config.stage3.arch)   or "amd64"
	subarch = repo.subarch or "x86-64"

	prof_ver = "23.0"
	if config.portage and config.portage.profile and config.portage.profile.name:
		for part in config.portage.profile.name.split("/"):
			if part and part[0].isdigit() and "." in part:
				prof_ver = part
				break

	return f"{mirror.rstrip('/')}/releases/{arch}/binpackages/{prof_ver}/{subarch}/"


def _setup_binrepos(config: GentlyConfig, runner: Runner) -> None:
	"""Write /etc/portage/binrepos.conf/<name>.conf for each entry in portage.binrepos.

	This is the modern Portage 3.x way to configure binary package hosts,
	mirroring how portage.repos maps to repos.conf entries.
	"""
	if not (config.portage and config.portage.binrepos):
		return

	binrepos_dir = "/etc/portage/binrepos.conf"
	runner.run_shell(f"mkdir -p {binrepos_dir}", phase=PHASE_KEY, chroot=True)

	needs_getuto = False
	for repo in config.portage.binrepos:
		if not repo.name:
			continue

		sync_uri         = repo.sync_uri or _binrepos_sync_uri(config, repo)
		binrepos_file = f"{binrepos_dir}/{repo.name}.conf"
		priority         = repo.priority if repo.priority is not None else 9999
		verify_signature = repo.verify_signature if repo.verify_signature is not None else True

		lines = [
			f"[{repo.name}]",
			f"priority = {priority}",
			f"sync-uri = {sync_uri}",
			f"verify-signature = {'true' if verify_signature else 'false'}",
		]
		for i, line in enumerate(lines):
			op = ">" if i == 0 else ">>"
			runner.run_shell(
				f"echo {shlex.quote(line)} {op} {binrepos_file}",
				phase=PHASE_KEY,
				chroot=True,
			)

		if verify_signature:
			needs_getuto = True

	# Set up the Gentoo binary keyring once if any repo requires signature verification.
	if needs_getuto:
		runner.run_shell("getuto", phase=PHASE_KEY, chroot=True)


def execute(config: GentlyConfig, runner: Runner) -> None:
	"""Portage phase: configure Portage and sync the tree."""
	# 1. Synchronize the Portage tree first.
	_setup_portage(config, runner)

	# 2. Then configure distcc (which needs the tree to be available).
	_setup_distcc(config, runner)

	# 3. Write the optimized make.conf.
	_write_makeconf(config, runner)

	# 4. Write binrepos.conf if a binary host is configured (modern Portage 3.x API).
	_setup_binrepos(config, runner)

	# 5. Detect and write CPU flags into package.use/00cpu-flags.
	_setup_cpu_flags(config, runner)

	# 6. Write package-specific configuration (accept_keywords, accept_license, world update).
	_write_package_config(config, runner)
