# Gently

Configuration-driven Gentoo Linux installer. Define your decisions in a TOML file, launch Gently, and the system installs itself.

Gently works in two modes:

- **Interactive**: launches a form for each configuration section that is not complete. Fields already present in the config are pre-filled automatically. Once data collection is done, the installation begins without further intervention.
- **Unattended**: if the configuration file has all required data, Gently asks no questions. Useful for repeatable or automated installations.

---

## Requirements

- Python 3.11 or higher
- Live environment with access to `parted`, `mkfs`, `tar` and `gpg`
- Network connectivity already configured in the live environment

Gently bundles its own dependencies in `vendor/`. Nothing needs to be installed in the live environment before running it.

---

## Quick start

```bash
# Clone the repository in the live environment
git clone https://git.yawin.es/personal/gently
cd gently

# Copy and edit the configuration file (optional)
cp config.toml.example config.toml
$EDITOR config.toml

# Launch the installer
python gently.py

# Or specify an alternative configuration file
python gently.py --config /path/to/my-config.toml
```

If `config.toml` does not exist or is empty, Gently starts directly in interactive mode and asks for all required data.

---

## Configuration file

The configuration file is a TOML with sections that correspond to the installation phases. All sections are optional: anything missing will be asked interactively.

The full reference is in [`config.toml.example`](config.toml.example). Below is a minimal working example:

```toml
[system]
hostname = "merry"
timezone = "Europe/Madrid"
locale   = "es_ES.UTF-8"
locales  = ["es_ES.UTF-8 UTF-8", "en_US.UTF-8 UTF-8"]
keymap   = "es"
lang     = "es_ES.UTF-8"

[stage3]
arch    = "amd64"
variant = "openrc"
mirror  = "https://distfiles.gentoo.org"

[[disks]]
id              = "primary"
device          = "/dev/sda"
partition_table = "gpt"
boot_mode       = "uefi"

[[disks.partitions]]
label      = "boot"
size       = "512M"
filesystem = "vfat"
mount      = "/boot/efi"
flags      = ["esp"]

[[disks.partitions]]
label      = "swap"
size       = "8G"
filesystem = "swap"

[[disks.partitions]]
label         = "root"
size          = "remaining"
filesystem    = "ext4"
mount         = "/"
mount_options = "noatime"

[portage]
cflags          = "-O2 -pipe -march=native"
cxxflags        = "-O2 -pipe -march=native"
makeopts        = "-j8"
use             = ["alsa", "X", "-bluetooth"]
accept_keywords = "amd64"
accept_license  = "@FREE"

[portage.profile]
name = "default/linux/amd64/23.0/desktop"

[kernel]
method = "binary"

[bootloader]
type = "grub"

[bootloader.grub]
platforms = ["efi-64"]
timeout   = 5

[services.roles]
network = "NetworkManager"
ssh     = "openssh"
cron    = "cronie"
logging = "sysklogd"
ntp     = "chrony"

[[users.accounts]]
name   = "yawin"
groups = ["wheel", "audio", "video", "usb", "plugdev"]
shell  = "/bin/bash"
```

### Passwords

Gently supports three ways to manage passwords, in order of priority:

1. **External file**: `credentials_file = "/path/to/credentials.toml"` or `credentials_file_shadow = "/path/to/credentials.toml"` in the `[users]` section. Can be excluded from version control while keeping the rest of the config in the repository. Its format is `user:password`. `credentials_file` holds passwords in plain text, while `credentials_file_shadow` uses `/etc/shadow` format.

2. **Inline SHA-512 hash**: `password_hash = "$6$salt$hash..."` in `/etc/shadow` format. Generated with `openssl passwd -6` or `mkpasswd -m sha-512`.

3. **Inline plain text**: `password = "text"`. Convenient for testing, not recommended for configs that will be versioned.

If none is specified, Gently prompts for the password interactively when it reaches that section.

### Stage3

Gently can obtain the stage3 in three ways, in order of priority:

1. **Local path**: `local_path = "/root/stage3-amd64-openrc-20250101.tar.xz"`. The installer does not access the network to download it.

2. **Explicit URL**: `tarball_url = "https://..."`. Useful for reproducible installations that require a specific version.

3. **Automatic download**: if neither of the above is specified, Gently queries the Gentoo autobuilds and downloads the latest stage3 available for the configured architecture and variant.

---

## Distcc

If you have machines on the network running `distccd`, Gently can use them during compilation. Add the `[distcc]` section to the config:

```toml
[distcc]
enabled           = true
hosts             = ["192.168.1.10/8,lzo", "192.168.1.11/4,lzo", "localhost/2"]
pump_mode         = false
install_on_target = true
```

Gently automatically calculates the `-j` value for `MAKEOPTS` by summing the slots declared in `hosts`. This can be overridden with `makeopts_jobs`.

Hosts must have `distccd` running and accessible before launching Gently.

---

## Future improvements

The current version implements a complete Gentoo base system installation with a single disk. The following features are planned for later versions and are not currently available:

- Multiple disks
- LUKS encryption
- btrfs subvolumes
- Kernels with `menuconfig` or custom `.config`
- `systemd-boot` bootloader
- Desktop environment
- Configuration of the installation environment from the installer itself

---

## Project structure

```
gently/
├── gently.py           # Entry point
├── config.toml.example # Fully documented reference config
├── ROADMAP.md          # Development roadmap
├── vendor/             # Bundled dependencies
├── model/              # Data model and validation
├── ui/                 # Terminal interface (curses)
├── installer/          # Installation phases
└── util/               # Logger and utilities
```

---

## Installation log

Gently writes a complete log to `/tmp/gently.log` throughout the entire execution,
including the output of all system commands. If the installation fails,
that file contains the full error context.

---