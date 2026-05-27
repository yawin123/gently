"""
Integration tests for collect() — no TTY required.

A StubBackend replaces CursesBackend and returns synthetic values
derived from each FormSpec, so all 10 forms can run unattended.

Run with:
    python3 tests/test_integration.py
"""
from __future__ import annotations

import sys
import os

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "vendor"))

from typing import Any

from model.config import GentlyConfig
from ui.abstract import FormSpec, UIBackend
from gently import collect, FORMS

# ---------------------------------------------------------------------------
# Hardcoded values for required text fields that would otherwise be empty
# ---------------------------------------------------------------------------
_TEXT_OVERRIDES: dict[str, Any] = {
    "hostname":    "testbox",
    "locale":      "en_US.UTF-8",
    "keymap":      "us",
    "lang":        "en_US.UTF-8",
    "device":      "/dev/sda",
    "label":       "root",
    "size":        "100%",
    "variant":     "openrc",
    "mirror":      "https://distfiles.gentoo.org",
    "root_password": "testpassword",
    "root_password_confirm": "testpassword",
}


def _auto_values(spec: FormSpec) -> dict[str, Any]:
    """Return a plausible response dict for a FormSpec without user interaction."""
    result: dict[str, Any] = {}
    for f in spec.fields:
        if f.type == "choice":
            # Prefer the declared default if it's in options; else first option
            if f.default and f.options and f.default in f.options:
                result[f.key] = f.default
            elif f.options:
                result[f.key] = f.options[0]
            else:
                result[f.key] = f.default
        elif f.type == "text":
            if f.default is not None:
                result[f.key] = f.default
            elif f.key in _TEXT_OVERRIDES:
                result[f.key] = _TEXT_OVERRIDES[f.key]
            elif f.required:
                result[f.key] = "testvalue"
            else:
                result[f.key] = None
        elif f.type == "password":
            if f.key in _TEXT_OVERRIDES:
                result[f.key] = _TEXT_OVERRIDES[f.key]
            elif f.required:
                result[f.key] = "testpassword"
            else:
                result[f.key] = None
        elif f.type == "bool":
            result[f.key] = f.default if f.default is not None else False
        elif f.type == "int":
            result[f.key] = f.default if f.default is not None else 1
        elif f.type == "list":
            result[f.key] = f.default if f.default is not None else []
        elif f.type == "subsection":
            result[f.key] = f.default
    return result


class StubBackend(UIBackend):
    """Non-interactive UIBackend for integration tests.

    Tracks how many times each method is called so tests can make assertions
    about the behaviour of collect().
    """

    def __init__(self) -> None:
        self.show_form_calls:    list[str] = []   # form titles
        self.show_info_calls:    list[str] = []   # info titles
        self.show_confirm_calls: list[str] = []   # confirm messages
        self.show_subsection_calls: list[str] = []
        self._confirm_index = 0
        self._partition_section_index = 0
        self._disk_form_calls = 0

    # --- UIBackend interface ------------------------------------------------

    def show_form(self, spec: FormSpec) -> dict[str, Any]:
        self.show_form_calls.append(spec.title)
        if spec.title == "Disk layout — disk settings":
            self._disk_form_calls += 1
            values = _auto_values(spec)
            if self._disk_form_calls == 1:
                return {
                    "__action__": "subsection",
                    "__field__": "partitions_editor",
                    "__values__": values,
                }
        return _auto_values(spec)

    def show_confirm(self, message: str) -> bool:
        self.show_confirm_calls.append(message)
        return True

    def show_subsection(self, title: str, partitions: list) -> str:
        self.show_subsection_calls.append(title)
        self._partition_section_index += 1
        return "add" if self._partition_section_index == 1 else "done"

    def show_summary(self, sections: list) -> str:
        return "install"

    def show_progress(self, phase: str, message: str) -> None:
        pass

    def show_error(self, message: str) -> None:
        pass

    def show_info(self, title: str, lines: list[str]) -> None:
        self.show_info_calls.append(title)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_collect_empty_config_completes_all():
    """collect() with empty config runs all 10 forms and produces a complete config."""
    config = GentlyConfig()
    backend = StubBackend()
    result = collect(config, backend)

    for form in FORMS:
        assert form.is_complete(result), \
            f"{type(form).__name__} is not complete after collect()"

    print("PASS  collect(empty_config): all 10 sections complete")


def test_collect_empty_config_spot_checks():
    """Verify specific field values populated by the stub."""
    config = GentlyConfig()
    backend = StubBackend()
    result = collect(config, backend)

    assert result.system.hostname == "testbox"
    assert result.system.timezone is not None
    assert result.stage3.arch is not None
    assert result.stage3.variant == "openrc"
    assert result.disks and result.disks[0].device is not None
    assert result.disks[0].partitions, "At least one partition must exist"
    assert result.portage.cflags is not None
    assert result.kernel.method is not None
    assert result.bootloader.type is not None
    assert result.services.roles.network is not None
    assert result.users is not None
    # root password was hashed
    root = next((a for a in result.users.accounts if a.name == "root"), None)
    assert root is not None and (root.password_hash is not None or root.password is not None)

    print("PASS  collect(empty_config): spot-check values correct")


def test_collect_full_config_skips_all_forms():
    """collect() with a fully complete config never calls show_form()."""
    from tests.test_forms import _complete_config

    config = _complete_config()
    backend = StubBackend()
    collect(config, backend)

    assert backend.show_form_calls == [], \
        f"Expected no show_form calls, got: {backend.show_form_calls}"
    # One show_info call per form section
    assert len(backend.show_info_calls) == len(FORMS), \
        f"Expected {len(FORMS)} show_info calls, got {len(backend.show_info_calls)}"

    print(f"PASS  collect(full_config): 0 forms shown, {len(FORMS)} sections confirmed")


def test_collect_partial_config_runs_only_missing():
    """collect() with some sections filled only runs forms for the incomplete ones."""
    from tests.test_forms import _complete_config
    config = _complete_config()
    # Remove two sections to force those forms to run
    config.kernel = None
    config.bootloader = None

    backend = StubBackend()
    collect(config, backend)

    # Only kernel and bootloader forms should have been shown
    assert len(backend.show_form_calls) == 2, \
        f"Expected 2 show_form calls, got {len(backend.show_form_calls)}: {backend.show_form_calls}"
    assert len(backend.show_info_calls) == len(FORMS) - 2

    print(f"PASS  collect(partial_config): only 2 missing forms run")


def test_collect_returns_new_config_not_mutated():
    """collect() should not silently drop the values from complete sections."""
    from tests.test_forms import _complete_config
    config = _complete_config()
    original_hostname = config.system.hostname

    backend = StubBackend()
    result = collect(config, backend)

    assert result.system.hostname == original_hostname, \
        "Completed section was overwritten by collect()"

    print("PASS  collect: pre-existing values preserved")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    test_collect_empty_config_completes_all()
    test_collect_empty_config_spot_checks()
    test_collect_full_config_skips_all_forms()
    test_collect_partial_config_runs_only_missing()
    test_collect_returns_new_config_not_mutated()
    print()
    print("All integration tests passed.")
