"""installer/kernel.py — Kernel installation phase.

Installs the kernel inside the chroot. Assumes chroot_prep has already run
(runner.chroot_path is set to MOUNTPOINT).

Supported methods in v1:
  installkernel — distribution kernel via emerge
                  binary=true  → sys-kernel/gentoo-kernel-bin  (prebuilt, no local compilation)
                  binary=false → sys-kernel/gentoo-kernel      (compiled locally with Portage)
                  
                  Both variants use sys-kernel/installkernel for post-install tasks:
                  - Copies kernel image to /boot or /efi
                  - Generates initramfs (if dracut USE flag is enabled)
                  - Updates bootloader configuration (GRUB, systemd-boot, etc.)

Methods reserved for future versions (raise a clear error in v1):
  menuconfig — interactive kernel configuration via menuconfig/nconfig
  custom     — use a pre-existing .config file provided by the user
  genkernel  — use genkernel --automagic to auto-generate .config then compile
"""
from __future__ import annotations

import shlex

from installer.runner import Runner, RunnerError
from model.config import GentlyConfig

PHASE_KEY = "kernel"

_RESERVED_METHODS: frozenset[str] = frozenset({"menuconfig", "custom", "genkernel"})
_SUPPORTED_METHODS: frozenset[str] = frozenset({"installkernel"})


class KernelError(RunnerError):
    pass


def _execute_installkernel(config: GentlyConfig, runner: Runner) -> None:
    """Execute the installkernel method.
    
    Installs a distribution kernel (binary or source) using emerge.
    The package's installkernel hook handles:
      - Copying kernel image to /boot or /efi
      - Generating initramfs (if dracut USE flag is enabled)
      - Updating bootloader configuration
    
    This method requires sys-kernel/installkernel[dracut] to be enabled.
    """
    kernel = config.kernel
    
    # Ensure installkernel has dracut USE flag
    package_use_dir = "/etc/portage/package.use"
    installkernel_use_file = f"{package_use_dir}/installkernel"
    
    # Create the package.use directory if it doesn't exist
    runner.run_shell(f"mkdir -p {shlex.quote(package_use_dir)}", phase=PHASE_KEY, chroot=True)
    
    # Write the USE flag configuration
    runner.run_shell(f"echo 'sys-kernel/installkernel dracut' > {shlex.quote(installkernel_use_file)}", phase=PHASE_KEY, chroot=True)
    
    # installkernel: binary=True (default) → prebuilt; binary=False → local compile
    use_binary = kernel.binary if (kernel is not None and kernel.binary is not None) else True
    package = "sys-kernel/gentoo-kernel-bin" if use_binary else "sys-kernel/gentoo-kernel"
    
    runner.run_shell(
        f"emerge {package}",
        phase=PHASE_KEY,
        chroot=True,
    )
    
    # Firmware packages (optional, independent of kernel method)
    _install_firmware(config, runner)


def _execute_menuconfig(config: GentlyConfig, runner: Runner) -> None:
    """Execute the menuconfig method (v2+).
    
    Provides interactive kernel configuration via menuconfig/nconfig.
    The user can customize kernel options before compilation.
    
    Requires:
      - kernel.config_path: path to .config file (optional, for base config)
      - kernel.extra_modules: additional modules to force-include
    
    This method is reserved for future implementation.
    """
    raise KernelError(
        "Kernel method 'menuconfig' is not implemented in v1. "
        "This will allow interactive kernel configuration via menuconfig/nconfig. "
        "Use 'installkernel' in the meantime."
    )


def _execute_custom(config: GentlyConfig, runner: Runner) -> None:
    """Execute the custom method (v2+).
    
    Uses a pre-existing .config file provided by the user.
    The kernel is compiled with the user's custom configuration.
    
    Requires:
      - kernel.custom.config_path: path to .config file
    
    This method is reserved for future implementation.
    """
    raise KernelError(
        "Kernel method 'custom' is not implemented in v1. "
        "This will allow using a custom .config file for kernel compilation. "
        "Use 'installkernel' in the meantime."
    )


def _execute_genkernel(config: GentlyConfig, runner: Runner) -> None:
    """Execute the genkernel method (v2+).
    
    Uses genkernel --automagic to auto-generate a .config file
    and compile the kernel with sensible defaults.
    
    Requires:
      - kernel.config_path: path to .config file (optional override)
      - kernel.extra_modules: additional modules to force-include
    
    This method is reserved for future implementation.
    """
    raise KernelError(
        "Kernel method 'genkernel' is not implemented in v1. "
        "This will use genkernel to auto-generate a .config and compile the kernel. "
        "Use 'installkernel' in the meantime."
    )


def _install_firmware(config: GentlyConfig, runner: Runner) -> None:
    """Install firmware packages as specified in the kernel configuration.
    
    These packages provide firmware blobs required by various hardware:
      - linux-firmware: WiFi, GPU, and other device firmware (required by most systems)
      - intel-microcode: CPU microcode updates for Intel processors
      - sof-firmware: Sound Open Firmware for Intel audio DSPs
    
    Installation is independent of the kernel method (binary or source).
    """
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
            f"emerge {' '.join(pkgs)}",
            phase=PHASE_KEY,
            chroot=True,
        )

def execute(config: GentlyConfig, runner: Runner) -> None:
    """Execute the kernel installation phase.
    
    Dispatches to the appropriate method handler based on config.kernel.method:
      - installkernel: distribution kernel via emerge (v1)
      - menuconfig: interactive kernel configuration (v2+)
      - custom: use a pre-existing .config file (v2+)
      - genkernel: use genkernel --automagic (v2+)
    
    For reserved methods (menuconfig, custom, genkernel):
      - Raise a clear error explaining they require v2+
    """
    kernel = config.kernel
    method = kernel.method if kernel is not None else None

    if not method:
        raise KernelError(
            "kernel.method is not set. Add 'method = \"installkernel\"' under [kernel]."
        )

    if method in _RESERVED_METHODS:
        raise KernelError(
            f"Kernel method '{method}' is not implemented in v1. "
            f"Available in v1: 'installkernel'. "
            f"'{method}' will be available in a future version."
        )

    if method not in _SUPPORTED_METHODS:
        raise KernelError(
            f"Unknown kernel method '{method}'. "
            f"Supported in v1: {', '.join(sorted(_SUPPORTED_METHODS))}. "
            f"Reserved for future versions: {', '.join(sorted(_RESERVED_METHODS))}."
        )

    # Dispatch to method-specific handler
    method_handlers: dict[str, callable] = {
        "installkernel": _execute_installkernel,
    }

    handler = method_handlers.get(method)
    if handler is None:
        raise KernelError(
            f"No handler registered for kernel method '{method}'. "
            f"Available handlers: {', '.join(sorted(method_handlers.keys()))}."
        )

    handler(config, runner)
