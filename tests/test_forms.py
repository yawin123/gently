"""
Unit tests for all 10 SectionForm implementations (Milestone 3).

Tests run without a TTY. No curses required.
Run with:
    python3 tests/test_forms.py
"""
import sys
import os

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "vendor"))

from model.config import (
    GentlyConfig, SystemConfig, Stage3Config, DiskConfig, PartitionConfig,
    PortageConfig, PortageProfileConfig, KernelConfig, BootloaderConfig,
    BootloaderGrubConfig, ServicesConfig, ServicesRolesConfig,
    UsersConfig, UserAccountConfig, PackagesConfig, DistccConfig,
)
from ui.forms.system     import SystemForm
from ui.forms.stage3     import Stage3Form
from ui.forms.disks      import DisksForm
from ui.forms.portage    import PortageForm
from ui.forms.kernel     import KernelForm
from ui.forms.bootloader import BootloaderForm
from ui.forms.services   import ServicesForm
from ui.forms.users      import UsersForm
from ui.forms.packages   import PackagesForm
from ui.forms.distcc     import DistccForm

import i18n as _i18n
_i18n.reload("en-us")  # tests compare against English strings

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _complete_config() -> GentlyConfig:
    """A GentlyConfig where every section satisfies is_complete."""
    return GentlyConfig(
        system=SystemConfig(
            hostname="testbox", timezone="UTC",
            locale="en_US.UTF-8", keymap="us", lang="en_US.UTF-8",
        ),
        stage3=Stage3Config(arch="amd64", variant="openrc", mirror="https://distfiles.gentoo.org"),
        disks=[DiskConfig(
            device="/dev/sda", partition_table="gpt", boot_mode="uefi",
            partitions=[PartitionConfig(label="root", size="100%", filesystem="ext4")],
        )],
        portage=PortageConfig(
            cflags="-O2", makeopts="-j4", use=[],
            accept_keywords="amd64", accept_license="@FREE",
            profile=PortageProfileConfig(name="default/linux/amd64/23.0"),
        ),
        kernel=KernelConfig(method="genkernel"),
        bootloader=BootloaderConfig(type="grub", grub=BootloaderGrubConfig()),
        services=ServicesConfig(roles=ServicesRolesConfig(network="netifrc")),
        users=UsersConfig(accounts=[UserAccountConfig(name="root", password="secret")]),
        packages=PackagesConfig(),
        distcc=DistccConfig(enabled=False),
    )


ALL_FORMS = [
    SystemForm(), Stage3Form(), DisksForm(), PortageForm(),
    KernelForm(), BootloaderForm(), ServicesForm(), UsersForm(),
    PackagesForm(), DistccForm(),
]

# ---------------------------------------------------------------------------
# 1. is_complete — empty config
# ---------------------------------------------------------------------------

def test_is_complete_empty_config():
    config = GentlyConfig()
    expected_false = [SystemForm, Stage3Form, DisksForm, PortageForm,
                      KernelForm, BootloaderForm, ServicesForm, UsersForm]
    expected_true  = [PackagesForm, DistccForm]

    for cls in expected_false:
        assert not cls().is_complete(config), f"{cls.__name__}.is_complete(empty) should be False"
    for cls in expected_true:
        assert cls().is_complete(config),     f"{cls.__name__}.is_complete(empty) should be True"
    print("PASS  is_complete(empty_config): correct for all 10 forms")


# ---------------------------------------------------------------------------
# 2. is_complete — full config
# ---------------------------------------------------------------------------

def test_is_complete_full_config():
    config = _complete_config()
    for form in ALL_FORMS:
        assert form.is_complete(config), \
            f"{type(form).__name__}.is_complete(full) should be True"
    print("PASS  is_complete(full_config): True for all 10 forms")


# ---------------------------------------------------------------------------
# 3. build_form — sanity checks
# ---------------------------------------------------------------------------

def test_build_form_sanity():
    config = GentlyConfig()
    for form in ALL_FORMS:
        spec = form.build_form(config)
        assert spec.title, f"{type(form).__name__}: title empty"
        assert spec.fields, f"{type(form).__name__}: no fields"
        for f in spec.fields:
            assert f.key,   f"{type(form).__name__}: field missing key"
            assert f.label, f"{type(form).__name__}: field missing label"
            assert f.type in ("text", "password", "choice", "bool", "list", "int", "subsection"), \
                f"{type(form).__name__}.{f.key}: unknown type {f.type!r}"
    print("PASS  build_form: all forms return valid FormSpec for empty config")


def test_build_form_system_extra_locales_options():
    spec = SystemForm().build_form(GentlyConfig())
    locales_field = next(f for f in spec.fields if f.key == "locales")
    assert locales_field.type == "list"
    assert locales_field.options, "SystemForm.locales should expose filtered options"
    print("PASS  SystemForm.build_form (extra locales options)")


# ---------------------------------------------------------------------------
# 4. apply — round-trip
# ---------------------------------------------------------------------------

def _defaults_from_form(form, config):
    """Simulate _NoopBackend: return all field defaults from build_form."""
    spec = form.build_form(config)
    return {f.key: f.default for f in spec.fields}


def test_apply_system():
    config = GentlyConfig()
    values = {"hostname": "myhost", "timezone": "UTC",
              "locale": "en_US.UTF-8", "locales": None,
              "keymap": "us", "lang": "en_US.UTF-8"}
    config = SystemForm().apply(config, values)
    assert SystemForm().is_complete(config)
    assert config.system.hostname == "myhost"
    print("PASS  SystemForm.apply")


def test_apply_stage3():
    config = GentlyConfig()
    values = {"arch": "amd64", "variant": "openrc",
              "mirror": "https://distfiles.gentoo.org",
              "local_path": None, "tarball_url": None, "verify_signature": True}
    config = Stage3Form().apply(config, values)
    assert Stage3Form().is_complete(config)
    assert config.stage3.arch == "amd64"
    print("PASS  Stage3Form.apply")


def test_apply_kernel():
    config = GentlyConfig()
    values = {"method": "genkernel", "config_path": None, "extra_modules": None}
    config = KernelForm().apply(config, values)
    assert KernelForm().is_complete(config)
    assert config.kernel.method == "genkernel"
    print("PASS  KernelForm.apply")


def test_apply_bootloader():
    config = GentlyConfig()
    values = {"type": "grub", "grub_install_disk": None,
              "grub_timeout": 5, "grub_cmdline_extra": None}
    config = BootloaderForm().apply(config, values)
    assert BootloaderForm().is_complete(config)
    assert config.bootloader.type == "grub"
    assert config.bootloader.grub is not None
    print("PASS  BootloaderForm.apply")


def test_apply_bootloader_none_type():
    config = GentlyConfig()
    values = {"type": "none", "grub_install_disk": None,
              "grub_timeout": None, "grub_cmdline_extra": None}
    config = BootloaderForm().apply(config, values)
    assert BootloaderForm().is_complete(config)
    assert config.bootloader.grub is None
    print("PASS  BootloaderForm.apply (type=none)")


def test_apply_distcc_disabled():
    config = GentlyConfig()
    values = {"enabled": False, "hosts": None, "makeopts_jobs": None,
              "pump_mode": False, "install_on_target": False}
    config = DistccForm().apply(config, values)
    assert DistccForm().is_complete(config)
    print("PASS  DistccForm.apply (disabled)")


def test_apply_distcc_enabled():
    config = GentlyConfig()
    values = {"enabled": True, "hosts": ["192.168.1.10/4"],
              "makeopts_jobs": None, "pump_mode": False, "install_on_target": False}
    config = DistccForm().apply(config, values)
    assert DistccForm().is_complete(config)
    assert config.distcc.enabled is True
    print("PASS  DistccForm.apply (enabled with hosts)")


def test_apply_services():
    config = GentlyConfig()
    values = {"network": "netifrc", "cron": None, "logging": "sysklogd",
              "ssh": "openssh", "ntp": None, "firewall": None,
              "printing": None, "bluetooth": None, "extra_enable": []}
    config = ServicesForm().apply(config, values)
    assert ServicesForm().is_complete(config)
    assert config.services.roles.network == "netifrc"
    print("PASS  ServicesForm.apply")


def test_apply_portage():
    config = GentlyConfig()
    values = {"cflags": "-O2", "cxxflags": None, "makeopts": "-j4",
              "cpu_flags": None, "use": ["X", "alsa"],
              "accept_keywords": "amd64", "accept_license": "@FREE",
              "video_cards": None, "input_devices": None, "mirrors": None,
              "profile_name": "default/linux/amd64/23.0"}
    config = PortageForm().apply(config, values)
    assert PortageForm().is_complete(config)
    assert config.portage.use == ["X", "alsa"]
    print("PASS  PortageForm.apply")


def test_apply_portage_empty_use():
    """Explicit empty USE list [] should still satisfy is_complete."""
    config = GentlyConfig()
    values = {"cflags": "-O2", "cxxflags": None, "makeopts": "-j4",
              "cpu_flags": None, "use": [],
              "accept_keywords": "amd64", "accept_license": "@FREE",
              "video_cards": None, "input_devices": None, "mirrors": None,
              "profile_name": "default/linux/amd64/23.0"}
    config = PortageForm().apply(config, values)
    assert PortageForm().is_complete(config), "Empty USE list [] should satisfy is_complete"
    print("PASS  PortageForm.apply (empty USE list)")


def test_apply_users_password():
    config = GentlyConfig()
    values = {"credentials_file": None, "root_password": "secret"}
    config = UsersForm().apply(config, values)
    assert UsersForm().is_complete(config)
    root = next(a for a in config.users.accounts if a.name == "root")
    # Password must be hashed (hash stored, plaintext cleared)
    assert root.password_hash is not None
    assert root.password is None
    print("PASS  UsersForm.apply (password)")


def test_apply_users_credentials_file():
    config = GentlyConfig()
    values = {"credentials_file": "/root/shadow", "root_password": None}
    config = UsersForm().apply(config, values)
    assert UsersForm().is_complete(config)
    assert config.users.credentials_file == "/root/shadow"
    print("PASS  UsersForm.apply (credentials_file)")


def test_users_run_requires_root_auth():
    class ScriptedBackend:
        def __init__(self) -> None:
            self._calls = 0
            self.errors: list[str] = []

        def show_form(self, spec):
            self._calls += 1
            if self._calls == 1:
                return {
                    "credentials_file": None,
                    "root_password": None,
                    "root_password_confirm": None,
                }
            return {
                "credentials_file": None,
                "root_password": "secret",
                "root_password_confirm": "secret",
            }

        def show_error(self, title_key, message, ok_key):
            self.errors.append(message)

        def interrupt(self):
            raise SystemExit(0)

    backend = ScriptedBackend()
    result = UsersForm().run(GentlyConfig(), backend)
    assert backend.errors, "Expected validation error when leaving without root auth"
    assert UsersForm().is_complete(result)
    print("PASS  UsersForm.run (requires root auth)")


def test_users_run_requires_password_match():
    class ScriptedBackend:
        def __init__(self) -> None:
            self._calls = 0
            self.errors: list[str] = []

        def show_form(self, spec):
            self._calls += 1
            if self._calls == 1:
                return {
                    "credentials_file": None,
                    "root_password": "secret1",
                    "root_password_confirm": "secret2",
                }
            return {
                "credentials_file": None,
                "root_password": "secret",
                "root_password_confirm": "secret",
            }

        def show_error(self, title_key, message, ok_key):
            self.errors.append(message)

        def interrupt(self):
            raise SystemExit(0)

    backend = ScriptedBackend()
    result = UsersForm().run(GentlyConfig(), backend)
    assert any("confirmation does not match" in m.lower() for m in backend.errors)
    assert UsersForm().is_complete(result)
    print("PASS  UsersForm.run (requires password confirmation match)")


def test_apply_packages():
    config = GentlyConfig()
    values = {"extra": ["vim", "git"], "source_only": None}
    config = PackagesForm().apply(config, values)
    assert PackagesForm().is_complete(config)
    assert config.packages.extra == ["vim", "git"]
    print("PASS  PackagesForm.apply")


def test_apply_disks():
    config = GentlyConfig()
    disk_values = {"device": "/dev/sda", "partition_table": "gpt",
                   "boot_mode": "uefi", "confirm_wipe": True}
    config = DisksForm().apply(config, disk_values)
    assert config.disks[0].device == "/dev/sda"
    print("PASS  DisksForm.apply (disk only)")


def test_disks_run_partition_cancel_then_add():
    class ScriptedBackend:
        def __init__(self) -> None:
            self.show_form_calls: list[str] = []
            self.show_subsection_calls: list[str] = []
            self._disk_form_calls = 0
            self._partition_attempt = 0

        def show_form(self, spec):
            self.show_form_calls.append(spec.title)
            if spec.title == "Disk layout — disk settings":
                self._disk_form_calls += 1
                if self._disk_form_calls == 1:
                    values = {
                        "device": "/dev/sda",
                        "partition_table": "gpt",
                        "boot_mode": "uefi",
                        "confirm_wipe": True,
                        "partitions_editor": "",
                    }
                    return {
                        "__action__": "subsection",
                        "__field__": "partitions_editor",
                        "__values__": values,
                    }
                return {
                    "device": "/dev/sda",
                    "partition_table": "gpt",
                    "boot_mode": "uefi",
                    "confirm_wipe": True,
                }

            self._partition_attempt += 1
            if self._partition_attempt == 1:
                return None
            return {
                "label": "root",
                "size": "100%",
                "filesystem": "ext4",
                "mount": "/",
                "mount_options": None,
                "flags": None,
            }

        def show_subsection(self, title, partitions):
            self.show_subsection_calls.append(title)
            return "add" if len(self.show_subsection_calls) < 3 else "done"

        def show_info(self, title, lines, ok_key):
            pass

        def show_confirm(self, message, yes_key, no_key):
            return True

        def interrupt(self):
            raise SystemExit(0)

    config = GentlyConfig()
    backend = ScriptedBackend()
    result = DisksForm().run(config, backend)

    assert result.disks[0].device == "/dev/sda"
    assert len(result.disks[0].partitions) == 1
    assert backend.show_form_calls == [
        "Disk layout — disk settings",
        "Disk layout — partition 1",
        "Disk layout — partition 1",
        "Disk layout — disk settings",
    ]
    assert backend.show_subsection_calls == [
        "Disk layout — partitions for /dev/sda",
        "Disk layout — partitions for /dev/sda",
        "Disk layout — partitions for /dev/sda",
    ]
    print("PASS  DisksForm.run (cancel partition then add)")


def test_disks_run_edit_existing_partition():
    class ScriptedBackend:
        def __init__(self) -> None:
            self.show_form_calls: list[str] = []
            self._disk_form_calls = 0
            self._sub_calls = 0

        def show_form(self, spec):
            self.show_form_calls.append(spec.title)
            if spec.title == "Disk layout — disk settings":
                self._disk_form_calls += 1
                if self._disk_form_calls == 1:
                    values = {
                        "device": "/dev/sda",
                        "partition_table": "gpt",
                        "boot_mode": "uefi",
                        "confirm_wipe": True,
                        "partitions_editor": "",
                    }
                    return {
                        "__action__": "subsection",
                        "__field__": "partitions_editor",
                        "__values__": values,
                    }
                return {
                    "device": "/dev/sda",
                    "partition_table": "gpt",
                    "boot_mode": "uefi",
                    "confirm_wipe": True,
                }
            return {
                "label": "rootfs",
                "size": "100%",
                "filesystem": "xfs",
                "mount": "/",
                "mount_options": None,
                "flags": None,
            }

        def show_subsection(self, title, items):
            self._sub_calls += 1
            return "edit:0" if self._sub_calls == 1 else "done"

        def show_info(self, title, lines, ok_key):
            pass

        def show_confirm(self, message, yes_key, no_key):
            return True

        def interrupt(self):
            raise SystemExit(0)

    config = GentlyConfig(disks=[DiskConfig(
        device="/dev/sda",
        partition_table="gpt",
        boot_mode="uefi",
        partitions=[PartitionConfig(label="root", size="100%", filesystem="ext4", mount="/")],
    )])
    backend = ScriptedBackend()
    result = DisksForm().run(config, backend)

    assert len(result.disks[0].partitions) == 1
    assert result.disks[0].partitions[0].label == "rootfs"
    assert result.disks[0].partitions[0].filesystem == "xfs"
    print("PASS  DisksForm.run (edit existing partition)")


def test_disks_run_delete_existing_partition():
    class ScriptedBackend:
        def __init__(self) -> None:
            self._disk_form_calls = 0
            self._sub_calls = 0

        def show_form(self, spec):
            if spec.title == "Disk layout — disk settings":
                self._disk_form_calls += 1
                if self._disk_form_calls == 1:
                    values = {
                        "device": "/dev/sda",
                        "partition_table": "gpt",
                        "boot_mode": "uefi",
                        "confirm_wipe": True,
                        "partitions_editor": "",
                    }
                    return {
                        "__action__": "subsection",
                        "__field__": "partitions_editor",
                        "__values__": values,
                    }
                return {
                    "device": "/dev/sda",
                    "partition_table": "gpt",
                    "boot_mode": "uefi",
                    "confirm_wipe": True,
                }
            return {"__action__": "delete"}

        def show_subsection(self, title, items):
            self._sub_calls += 1
            return "edit:0" if self._sub_calls == 1 else "done"

        def show_info(self, title, lines, ok_key):
            pass

        def show_confirm(self, message, yes_key, no_key):
            return True

        def interrupt(self):
            raise SystemExit(0)

    config = GentlyConfig(disks=[DiskConfig(
        device="/dev/sda",
        partition_table="gpt",
        boot_mode="uefi",
        partitions=[PartitionConfig(label="root", size="100%", filesystem="ext4", mount="/")],
    )])
    backend = ScriptedBackend()
    result = DisksForm().run(config, backend)

    assert result.disks[0].partitions == []
    print("PASS  DisksForm.run (delete existing partition)")


# ---------------------------------------------------------------------------
# 5. DisksForm.is_complete edge cases
# ---------------------------------------------------------------------------

def test_disks_is_complete_no_partitions():
    config = GentlyConfig(disks=[DiskConfig(
        device="/dev/sda", partition_table="gpt", boot_mode="uefi",
    )])
    assert not DisksForm().is_complete(config), \
        "Disk with no partitions must not be complete"
    print("PASS  DisksForm.is_complete: disk without partitions = False")


def test_disks_is_complete_incomplete_partition():
    config = GentlyConfig(disks=[DiskConfig(
        device="/dev/sda", partition_table="gpt", boot_mode="uefi",
        partitions=[PartitionConfig(label="root")],  # missing size and filesystem
    )])
    assert not DisksForm().is_complete(config)
    print("PASS  DisksForm.is_complete: partition missing size/filesystem = False")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    test_is_complete_empty_config()
    test_is_complete_full_config()
    test_build_form_sanity()
    test_build_form_system_extra_locales_options()
    test_apply_system()
    test_apply_stage3()
    test_apply_kernel()
    test_apply_bootloader()
    test_apply_bootloader_none_type()
    test_apply_distcc_disabled()
    test_apply_distcc_enabled()
    test_apply_services()
    test_apply_portage()
    test_apply_portage_empty_use()
    test_apply_users_password()
    test_apply_users_credentials_file()
    test_users_run_requires_root_auth()
    test_users_run_requires_password_match()
    test_apply_packages()
    test_apply_disks()
    test_disks_run_partition_cancel_then_add()
    test_disks_run_edit_existing_partition()
    test_disks_run_delete_existing_partition()
    test_disks_is_complete_no_partitions()
    test_disks_is_complete_incomplete_partition()
    print()
    print("All form tests passed.")
