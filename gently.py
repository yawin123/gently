import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "vendor"))

from model.config import load_config, save_config, ConfigError, GentlyConfig
from model.validators import validate_coherence
from ui.abstract import UIBackend
from ui.curses_backend import CursesBackend
from ui.forms.system import SystemForm
from ui.forms.stage3 import Stage3Form
from ui.forms.disks import DisksForm
from ui.forms.portage import PortageForm
from ui.forms.kernel import KernelForm
from ui.forms.bootloader import BootloaderForm
from ui.forms.services import ServicesForm
from ui.forms.users import UsersForm
from ui.forms.packages import PackagesForm
from ui.forms.distcc import DistccForm

FORMS = [
    SystemForm(),
    Stage3Form(),
    DisksForm(),
    PortageForm(),
    KernelForm(),
    BootloaderForm(),
    ServicesForm(),
    UsersForm(),
    PackagesForm(),
    DistccForm(),
]


def collect(config: GentlyConfig, backend: UIBackend) -> GentlyConfig:
    for form in FORMS:
        if form.is_complete(config):
            backend.show_info("✓", [f"{form.section_name} — configuration complete"])
        else:
            config = form.run(config, backend)
    return config


def main() -> None:
    backend = CursesBackend()
    config = load_config("config.toml")
    config = collect(config, backend)
    save_config(config, "config.toml")
    errors = validate_coherence(config)
    if errors:
        backend.show_error("\n".join(errors))
        sys.exit(1)
    # Milestone 5: run_installation(config, backend)


if __name__ == "__main__":
    main()
