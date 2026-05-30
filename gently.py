import sys
import os
from datetime import datetime
from typing import Any

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "vendor"))

from model.config import load_config, save_config, ConfigError, GentlyConfig
from model.validators import validate_coherence
from ui import create_backend
from ui.abstract import UIBackend
from installer.runner import LocalRunner, run_installation_interactive
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


def collect(config: GentlyConfig, backend: UIBackend) -> tuple[GentlyConfig, str]:
    """Show the section selection menu and return (config, action).

    action is "save_and_exit" or "install".
    """
    forms_by_key = {form.section_key: form for form in FORMS}

    while True:
        sections = [
            (f.section_key, f.section_name, f.is_complete(config))
            for f in FORMS
        ]
        all_complete = all(complete for _, _, complete in sections)

        action = backend.show_section_menu(sections, all_complete)

        if action in ("save_and_exit", "install"):
            return config, action

        if action.startswith("edit:"):
            key = action.split(":", 1)[1]
            form = forms_by_key.get(key)
            if form:
                config = form.run(config, backend)


def _build_summary_sections(config: GentlyConfig) -> list[tuple[str, dict[str, Any]]]:
    def _as_summary(value: Any) -> Any:
        if value is None:
            return None
        if isinstance(value, list):
            return [_as_summary(v) for v in value]
        if hasattr(value, "to_dict"):
            return _as_summary(value.to_dict())
        if isinstance(value, dict):
            result: dict[str, Any] = {}
            for k, v in value.items():
                mapped = _as_summary(v)
                if mapped is not None:
                    result[k] = mapped
            return result
        return value

    sections: list[tuple[str, dict[str, Any]]] = []
    data = _as_summary(config.to_dict()) or {}
    if isinstance(data, dict):
        for key in (
            "system", "stage3", "disks", "portage", "kernel",
            "bootloader", "services", "users", "packages", "distcc",
        ):
            value = data.get(key)
            if isinstance(value, dict):
                sections.append((key, value))
            elif isinstance(value, list):
                sections.append((key, {"items": value}))
    return sections


def main() -> None:
    backend = create_backend()
    config = load_config("config.toml")
    config, action = collect(config, backend)
    save_config(config, "config.toml")

    # Always persist a timestamped copy.
    saves_dir = os.path.join(os.path.dirname(__file__), "saves")
    os.makedirs(saves_dir, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    save_config(config, os.path.join(saves_dir, f"gently_{ts}.toml"))

    if action != "install":
        return

    errors = validate_coherence(config)
    if errors:
        backend.show_error("ui_error_title", "\n".join(errors), "ui_press_any_key")
        sys.exit(1)

    runner = LocalRunner(dry_run=False)
    try:
        run_installation_interactive(config, runner, backend)
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    main()
