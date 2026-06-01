"""installer/kernel.py — Kernel installation phase.

Installs the kernel inside the chroot. Assumes chroot_prep has already run
(runner.chroot_path is set to MOUNTPOINT).

Supported methods in v1:
  installkernel — distribution kernel
                  binary=true  → sys-kernel/gentoo-kernel-bin  (prebuilt, no local compilation)
                  binary=false → sys-kernel/gentoo-kernel      (compiled locally with Portage)

Methods reserved for future versions (raise a clear error in v1):
  menuconfig, custom
"""
from __future__ import annotations

from installer.runner import Runner, RunnerError
from model.config import GentlyConfig

PHASE_KEY = "kernel"

_RESERVED_METHODS: frozenset[str] = frozenset({"menuconfig", "custom"})
_SUPPORTED_METHODS: frozenset[str] = frozenset({"installkernel"})


class KernelError(RunnerError):
    pass


def execute(config: GentlyConfig, runner: Runner) -> None:
    kernel = config.kernel
    method = kernel.method if kernel is not None else None

    if not method:
        raise KernelError(
            "kernel.method is not set. Add 'method = \"installkernel\"' under [kernel]."
        )

    if method in _RESERVED_METHODS:
        raise KernelError(
            f"Kernel method '{method}' is not implemented in v1. Use 'installkernel'."
        )

    if method not in _SUPPORTED_METHODS:
        raise KernelError(
            f"Unknown kernel method '{method}'. "
            f"Supported in v1: {', '.join(sorted(_SUPPORTED_METHODS))}."
        )

    # installkernel: binary=True (default) → prebuilt; binary=False → local compile
    use_binary = kernel.binary if (kernel is not None and kernel.binary is not None) else True
    package = "sys-kernel/gentoo-kernel-bin" if use_binary else "sys-kernel/gentoo-kernel"
    runner.run_shell(
        f"emerge --oneshot {package}",
        phase=PHASE_KEY,
        chroot=True,
    )

    # Firmware packages (optional, independent of kernel method)
    _install_firmware(config, runner)


def _install_firmware(config: GentlyConfig, runner: Runner) -> None:
    kernel = config.kernel
    if kernel is None:
        return
    pkgs: list[str] = []
    if kernel.linux_firmware:
        pkgs.append("sys-kernel/linux-firmware")
    if kernel.intel_microcode:
        pkgs.append("sys-firmware/intel-microcode")
    if kernel.sof_firmware:
        pkgs.append("sys-firmware/sof-firmware")
    if pkgs:
        runner.run_shell(
            f"emerge --oneshot {' '.join(pkgs)}",
            phase=PHASE_KEY,
            chroot=True,
        )
