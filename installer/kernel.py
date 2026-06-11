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

from installer.partition import _get_uuid as _get_partition_uuid
from installer.runner import Runner, RunnerError
from model.config import DiskConfig, GentlyConfig

PHASE_KEY = "kernel"

_RESERVED_METHODS: frozenset[str] = frozenset({"menuconfig", "custom", "genkernel"})
_SUPPORTED_METHODS: frozenset[str] = frozenset({"installkernel"})


class KernelError(RunnerError):
    pass


def _get_root_partition_device(config: GentlyConfig) -> str:
    """Find the device path for the root partition (/).
    
    Searches through config.disks for a partition with mount == "/".
    Returns the device path (e.g., "/dev/sda3") which should have been
    saved during partition creation.
    
    Raises:
        KernelError: If no root partition is found or device path is missing.
    """
    for disk in config.disks:
        for partition in disk.partitions:
            if partition.mount == "/":
                if partition.device:
                    return partition.device
                # Fallback: calculate based on index if device not saved
                idx = disk.partitions.index(partition)
                return f"{disk.device}{idx + 1}"
    
    raise KernelError(
        "No root partition (mount='/') found in configuration. "
        "Add a partition with mount='/' under disks[].partitions[]."
    )


def _configure_installkernel_use_flags(config: GentlyConfig, runner: Runner) -> None:
    """Configure USE flags for sys-kernel/installkernel based on bootloader type.
    
    This function is agnostic to the kernel installation method and will be
    reused by future methods (menuconfig, custom, genkernel) in v2+.
    
    Args:
        config: The Gently configuration.
        runner: The Runner instance for executing commands.
    """
    bootloader_type = config.bootloader.type if config.bootloader else None
    
    # Start with dracut (required for initramfs generation)
    use_flags = ["dracut"]
    
    # Add bootloader-specific flags
    if bootloader_type == "grub":
        use_flags.append("grub")
    elif bootloader_type == "systemd-boot":
        use_flags.extend(["systemd", "systemd-boot"])
    elif bootloader_type == "efistub":
        use_flags.extend(["efistub", "uki"])
    # If None or unknown, use traditional /boot layout (no extra flags)
    
    # Write the USE flag configuration
    package_use_dir = "/etc/portage/package.use"
    installkernel_use_file = f"{package_use_dir}/installkernel"
    
    runner.run_shell(
        f"mkdir -p {shlex.quote(package_use_dir)}",
        phase=PHASE_KEY,
        chroot=True,
    )
    
    runner.run_shell(
        f"echo 'sys-kernel/installkernel {' '.join(use_flags)}' > {shlex.quote(installkernel_use_file)}",
        phase=PHASE_KEY,
        chroot=True,
    )


def _configure_dracut_chroot(config: GentlyConfig, runner: Runner) -> None:
    """Configure dracut to handle chroot detection for initramfs generation.
    
    When installkernel runs in a chroot, it reads /proc/cmdline from the livecd
    by default, which causes boot failures. This function configures dracut to
    use an explicit root=UUID parameter instead.
    
    This function is agnostic to the kernel installation method and will be
    reused by future methods (menuconfig, custom, genkernel) in v2+.
    
    Args:
        config: The Gently configuration.
        runner: The Runner instance for executing commands.
    
    Raises:
        KernelError: If no root partition is found or blkid fails.
    """
    # Get root device from configuration
    root_device = _get_root_partition_device(config)
    
    # Get UUID using blkid (run outside chroot, device is accessible)
    uuid = _get_partition_uuid(root_device, runner)
    if uuid is None:
        raise KernelError(
            f"Could not determine UUID for root device {root_device}. "
            "Please check that the device exists and has a filesystem."
        )
    
    # Create dracut config directory and file
    dracut_conf_dir = "/etc/dracut.conf.d"
    dracut_conf_file = f"{dracut_conf_dir}/00-installkernel.conf"
    
    runner.run_shell(
        f"mkdir -p {shlex.quote(dracut_conf_dir)}",
        phase=PHASE_KEY,
        chroot=True,
    )
    
    # Note: leading and trailing spaces are required by dracut
    runner.run_shell(
        f"echo 'kernel_cmdline=\" root=UUID={uuid} \"' > {shlex.quote(dracut_conf_file)}",
        phase=PHASE_KEY,
        chroot=True,
    )


def _configure_kernel_cmdline(config: GentlyConfig, runner: Runner) -> None:
    """Configure kernel command line for systemd-boot and EFI stub bootloaders.
    
    These bootloaders require /etc/kernel/cmdline to be set with the root=UUID
    parameter, along with a symlink in /etc/cmdline.d/.
    
    This function is agnostic to the kernel installation method and will be
    reused by future methods (menuconfig, custom, genkernel) in v2+.
    
    Args:
        config: The Gently configuration.
        runner: The Runner instance for executing commands.
    
    Raises:
        KernelError: If no root partition is found or blkid fails.
    """
    bootloader_type = config.bootloader.type if config.bootloader else None
    
    # Only configure for systemd-boot and efistub
    if bootloader_type not in ("systemd-boot", "efistub"):
        return
    
    # Get root device and UUID
    root_device = _get_root_partition_device(config)
    uuid = _get_partition_uuid(root_device, runner)
    if uuid is None:
        raise KernelError(
            f"Could not determine UUID for root device {root_device}. "
            "Please check that the device exists and has a filesystem."
        )
    
    # Create /etc/kernel/cmdline
    kernel_cmdline_file = "/etc/kernel/cmdline"
    cmdline_d_dir = "/etc/cmdline.d"
    cmdline_link = f"{cmdline_d_dir}/00-installkernel.conf"
    
    runner.run_shell(
        f"echo 'root=UUID={uuid} quiet splash' > {shlex.quote(kernel_cmdline_file)}",
        phase=PHASE_KEY,
        chroot=True,
    )
    
    runner.run_shell(
        f"mkdir -p {shlex.quote(cmdline_d_dir)}",
        phase=PHASE_KEY,
        chroot=True,
    )
    
    runner.run_shell(
        f"ln -sf {shlex.quote(kernel_cmdline_file)} {shlex.quote(cmdline_link)}",
        phase=PHASE_KEY,
        chroot=True,
    )


def _configure_dist_kernel_use(config: GentlyConfig, runner: Runner) -> None:
    """Enable the dist-kernel USE flag globally in make.conf.
    
    The Gentoo handbook strongly recommends enabling USE=\"dist-kernel\"
    globally when using a distribution kernel. Since installkernel always
    uses a distribution kernel, this flag is always added — no user
    configuration needed. Users who want dist-kernel with other methods
    can add it manually via [portage] use.
    
    This flag:
      - Triggers automatic rebuild of external kernel modules (ZFS, NVIDIA)
        when the kernel is updated
      - Triggers automatic regeneration of the initramfs when needed
    
    Args:
        config: The Gently configuration.
        runner: The Runner instance for executing commands.
    """
    make_conf = "/etc/portage/make.conf"
    
    runner.run_shell(
        f"grep -q '^USE=' {shlex.quote(make_conf)} && "
        f"sed -i 's/^USE=\"\\(.*\\)\"/USE=\"\\1 dist-kernel\"/' {shlex.quote(make_conf)} || "
        f"echo 'USE=\"dist-kernel\"' >> {shlex.quote(make_conf)}",
        phase=PHASE_KEY,
        chroot=True,
    )


def _configure_modules_sign(config: GentlyConfig, runner: Runner) -> None:
    """Enable modules-sign USE flag and signing keys for source kernels.
    
    The Gentoo handbook recommends signing kernel modules when compiling
    from source (sys-kernel/gentoo-kernel, binary=False). This function:
      - Adds modules-sign to the global USE flags in make.conf
      - Configures MODULES_SIGN_KEY and MODULES_SIGN_CERT if the user
        has provided signing keys via kernel.custom
    
    Default behaviour (kernel.modules_sign is None): enables the flag
    automatically for source-compiled kernels (binary=False).
    
    Args:
        config: The Gently configuration.
        runner: The Runner instance for executing commands.
    """
    kernel = config.kernel
    if kernel is None:
        return
    
    should_enable = kernel.modules_sign
    if should_enable is None:
        # Default: enable for source-compiled kernels
        should_enable = True
    
    if not should_enable:
        return
    
    make_conf = "/etc/portage/make.conf"
    
    # Append modules-sign to USE
    runner.run_shell(
        f"grep -q '^USE=' {shlex.quote(make_conf)} && "
        f"sed -i 's/^USE=\"\\(.*\\)\"/USE=\"\\1 modules-sign\"/' {shlex.quote(make_conf)} || "
        f"echo 'USE=\"modules-sign\"' >> {shlex.quote(make_conf)}",
        phase=PHASE_KEY,
        chroot=True,
    )
    
    # Configure signing keys if provided
    custom = kernel.custom
    if custom is not None and custom.sign_key:
        sign_key = custom.sign_key
        runner.run_shell(
            f"echo 'MODULES_SIGN_KEY=\"{sign_key}\"' >> {shlex.quote(make_conf)}",
            phase=PHASE_KEY,
            chroot=True,
        )
        if custom.sign_cert:
            sign_cert = custom.sign_cert
            runner.run_shell(
                f"echo 'MODULES_SIGN_CERT=\"{sign_cert}\"' >> {shlex.quote(make_conf)}",
                phase=PHASE_KEY,
                chroot=True,
            )
        if custom.sign_hash:
            sign_hash = custom.sign_hash
            runner.run_shell(
                f"echo 'MODULES_SIGN_HASH=\"{sign_hash}\"' >> {shlex.quote(make_conf)}",
                phase=PHASE_KEY,
                chroot=True,
            )


def _configure_secureboot(config: GentlyConfig, runner: Runner) -> None:
    """Enable secureboot USE flag and signing keys for Secure Boot.
    
    The Gentoo handbook describes signing the kernel image for Secure Boot.
    This function:
      - Adds secureboot to the global USE flags in make.conf
      - Configures SECUREBOOT_SIGN_KEY and SECUREBOOT_SIGN_CERT if the
        user has provided them via kernel.custom
    
    Secure Boot is optional and disabled by default (kernel.secureboot
    defaults to None, which means disabled).
    
    Args:
        config: The Gently configuration.
        runner: The Runner instance for executing commands.
    """
    kernel = config.kernel
    if kernel is None:
        return
    
    should_enable = kernel.secureboot
    if not should_enable:
        return
    
    make_conf = "/etc/portage/make.conf"
    
    # Append secureboot to USE
    runner.run_shell(
        f"grep -q '^USE=' {shlex.quote(make_conf)} && "
        f"sed -i 's/^USE=\"\\(.*\\)\"/USE=\"\\1 secureboot\"/' {shlex.quote(make_conf)} || "
        f"echo 'USE=\"secureboot\"' >> {shlex.quote(make_conf)}",
        phase=PHASE_KEY,
        chroot=True,
    )
    
    # Configure secureboot signing keys if provided
    custom = kernel.custom
    if custom is not None and custom.sign_key:
        sign_key = custom.sign_key
        runner.run_shell(
            f"echo 'SECUREBOOT_SIGN_KEY=\"{sign_key}\"' >> {shlex.quote(make_conf)}",
            phase=PHASE_KEY,
            chroot=True,
        )
        if custom.sign_cert:
            sign_cert = custom.sign_cert
            runner.run_shell(
                f"echo 'SECUREBOOT_SIGN_CERT=\"{sign_cert}\"' >> {shlex.quote(make_conf)}",
                phase=PHASE_KEY,
                chroot=True,
            )


def _execute_installkernel(config: GentlyConfig, runner: Runner) -> None:
    """Execute the installkernel method.
    
    Installs a distribution kernel (binary or source) using emerge.
    The package's installkernel hook handles:
      - Copying kernel image to /boot or /efi
      - Generating initramfs (if dracut USE flag is enabled)
      - Updating bootloader configuration
    
    This method orchestrates the following agnostic helpers:
      - _configure_installkernel_use_flags: sets USE flags based on bootloader
      - _configure_dracut_chroot: configures dracut for chroot detection
      - _configure_kernel_cmdline: sets kernel cmdline for systemd-boot/efistub
    
    This method also applies distribution-kernel-specific make.conf settings:
      - _configure_dist_kernel_use: enables dist-kernel USE flag globally
      - _configure_modules_sign: enables modules-sign for source kernels
      - _configure_secureboot: enables secureboot for Secure Boot support
    
    Args:
        config: The Gently configuration.
        runner: The Runner instance for executing commands.
    """
    kernel = config.kernel
    use_binary = kernel.binary if (kernel is not None and kernel.binary is not None) else True

    # Configure global make.conf USE flags for distribution kernels
    # Must run BEFORE configure helpers so USE=dist-kernel is active
    _configure_dist_kernel_use(config, runner)

    # Configure modules-sign for source-compiled kernels
    if not use_binary:
        _configure_modules_sign(config, runner)

    # Configure secureboot for source-compiled kernels (optional)
    if not use_binary:
        _configure_secureboot(config, runner)

    # Configure installkernel USE flags (agnostic to kernel method)
    _configure_installkernel_use_flags(config, runner)
    
    # Configure dracut for chroot detection (agnostic to kernel method)
    _configure_dracut_chroot(config, runner)
    
    # Configure kernel cmdline for systemd-boot/efistub (agnostic to kernel method)
    _configure_kernel_cmdline(config, runner)
    
    # installkernel-specific: install the kernel package
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
