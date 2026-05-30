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
# Distcc setup
# ---------------------------------------------------------------------------

def _setup_portage(config: GentlyConfig, runner: Runner) -> None:
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
	
	# Select profile if specified
	if config.portage and config.portage.profile and config.portage.profile.name:
		profile = config.portage.profile.name
		runner.run_shell(
			f"eselect profile set {shlex.quote(profile)}",
			phase=PHASE_KEY,
			chroot=True,
		)

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


def _calculate_makeopts(config: GentlyConfig, runner: Runner) -> str:
	"""Calculate MAKEOPTS based on distcc configuration and available CPUs.

	Respects config.portage.makeopts if explicitly set.
	Otherwise: '-jN -lN' for distcc, '-jN' for local.
	"""
	# 1. User-specified makeopts has priority
	if config.portage and config.portage.makeopts:
		return config.portage.makeopts
	
	# 2. Discover CPU count
	nproc = _get_nproc(runner)
	
	d = config.distcc
	if d and d.enabled:
		# Distcc: user can override jobs count
		if d.makeopts_jobs:
			jobs = d.makeopts_jobs
		else:
			jobs = nproc * 3
		return f"-j{jobs} -l{nproc}"
	else:
		jobs = nproc + 1
		return f"-j{jobs}"


def _discover_march(runner: Runner) -> str:
	"""Discover the CPU microarchitecture for -march.

	Runs `gcc -march=native -Q --help=target` INSIDE THE CHROOT to find
	what -march=native resolves to on the target machine.

	Returns the architecture (e.g., 'skylake') or 'native' as fallback.
	"""
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
	
	# Add distcc configuration if enabled
	if distcc_enabled and config.distcc:
		d = config.distcc
		if d.hosts:
			hosts_str = " ".join(d.hosts)
			lines.append(f'DISTCC_HOSTS="{hosts_str}"')
		# FEATURES: merge distcc with any user-specified features
		features_line = None
		for i, line in enumerate(lines):
			if line.startswith("FEATURES="):
				features_line = i
				break
		if features_line is not None:
			lines[features_line] = lines[features_line].rstrip('"') + ' distcc"'
		else:
			lines.append('FEATURES="distcc"')
	
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


def execute(config: GentlyConfig, runner: Runner) -> None:
	"""Portage phase: configure Portage and sync the tree."""
	# 1. Synchronize the Portage tree first.
	_setup_portage(config, runner)

	# 2. Then configure distcc (which needs the tree to be available).
	_setup_distcc(config, runner)

	# 3. Finally, write the optimized make.conf.
	_write_makeconf(config, runner)

	# 4. Write package-specific configuration.
	_write_package_config(config, runner)
