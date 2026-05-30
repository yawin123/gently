"""installer/chroot.py — Chroot Prep phase.

Prepares the /mnt/gentoo chroot environment so that all subsequent phases can
run commands directly inside the Gentoo system without any special handling:

1. Bind-mount the host virtual filesystems (proc, sys, dev, run) into the
   mountpoint.  Each mount is registered on the runner's cleanup stack so it
   is automatically unmounted when the runner finishes (or fails).
2. Copy the host's resolv.conf so DNS works inside the chroot.
3. Set runner.chroot_path = MOUNTPOINT, which causes run_shell() to
   transparently prefix every subsequent command with
   `chroot /mnt/gentoo /bin/bash -lc`.

After this phase completes, portage / kernel / system / … phases write plain
commands (e.g. "emerge --sync") and the runner takes care of the chroot wrap.
"""

from __future__ import annotations

import shlex

from model.config import GentlyConfig
from installer.runner import Runner, RunnerError


PHASE_KEY  = "chroot_prep"
MOUNTPOINT = "/mnt/gentoo"


class ChrootError(RunnerError):
	pass


# ---------------------------------------------------------------------------
# Virtual filesystem bind-mount specs
#   (mount_flags_and_source, target_suffix, needs_make_rslave)
# ---------------------------------------------------------------------------

_VIRT_MOUNTS: list[tuple[str, str, bool]] = [
	# proc: special pseudo-filesystem
	("--types proc /proc", "/proc", False),
	# sys: sysfs — rbind so devices are visible; rslave to avoid propagation back to host
	("--rbind /sys",       "/sys",  True),
	# dev: device nodes — rbind for full device access; rslave for same reason
	("--rbind /dev",       "/dev",  True),
	# run: runtime data (udev, etc.)
	("--bind /run",        "/run",  False),
]


def _mount_virt(runner: Runner) -> None:
	"""Mount the four virtual filesystems and register their cleanup entries."""
	for flags_and_src, suffix, rslave in _VIRT_MOUNTS:
		target = MOUNTPOINT + suffix
		runner.run_shell(f"mkdir -p {shlex.quote(target)}", phase=PHASE_KEY)
		runner.run_shell(
			f"mount {flags_and_src} {shlex.quote(target)}",
			phase=PHASE_KEY,
		)
		if rslave:
			runner.run_shell(
				f"mount --make-rslave {shlex.quote(target)}",
				phase=PHASE_KEY,
			)
		# Register cleanup — run_cleanup() clears chroot_path first, so this
		# umount runs on the host (not inside the chroot).
		_t = target
		runner.push_cleanup(
			f"umount {_t}",
			lambda t=_t: runner.run_shell(
				f"umount -l {shlex.quote(t)}", check=False, phase="cleanup"
			),
		)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def execute(config: GentlyConfig, runner: Runner) -> None:
	_mount_virt(runner)

	# Copy DNS configuration so the chroot can reach the network.
	runner.run_shell(
		f"cp --dereference /etc/resolv.conf {shlex.quote(MOUNTPOINT + '/etc/resolv.conf')}",
		phase=PHASE_KEY,
	)

	# Activate chroot mode.  From this point, runner.run_shell(chroot=True) wraps
	# every command with: chroot /mnt/gentoo /bin/bash -lc "..."
	runner.chroot_path = MOUNTPOINT
