"""
Unit tests for the collect() engine in gently.py.

Uses minimal in-process stubs — no curses, no TTY required.
Run with:
    python3 tests/test_collect.py
"""
import sys
import os

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "vendor"))

from model.config import GentlyConfig
from ui.abstract import UIBackend, FormSpec, FieldSpec
from ui.forms.base import SectionForm
from gently import collect

# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------

class _ScriptedBackend(UIBackend):
    """Backend driven by a sequence of pre-set show_section_menu return values."""

    def __init__(self, menu_script: list[str]):
        self._menu_script = list(menu_script)
        self.menu_calls: list[tuple[list, bool]] = []
        self.form_calls: list[str] = []

    def show_section_menu(self, sections, all_complete):
        self.menu_calls.append((sections, all_complete))
        return self._menu_script.pop(0)

    def show_form(self, form: FormSpec) -> dict:
        self.form_calls.append(form.title)
        return {f.key: f.default for f in form.fields}

    def show_summary(self, sections):
        return "save_and_exit"

    def show_subsection(self, title, partitions):
        return "done"

    def show_progress(self, phase, message):
        pass

    def show_error(self, title_key, message, ok_key):
        pass

    def show_confirm(self, message, yes_key, no_key) -> bool:
        return True

    def show_info(self, title, lines, ok_key):
        pass


class _CompleteForm(SectionForm):
    section_name = "Always complete"
    section_key  = "system"

    def is_complete(self, config):
        return True

    def build_form(self, config):
        raise AssertionError("build_form must not be called for a complete section")

    def apply(self, config, values):
        raise AssertionError("apply must not be called for a complete section")


class _IncompleteForm(SectionForm):
    section_name = "Needs input"
    section_key  = "stage3"

    def __init__(self, marker: str):
        self.marker = marker
        self.applied = False

    def is_complete(self, config):
        return False

    def build_form(self, config):
        return FormSpec(
            title=f"Form: {self.marker}",
            subtitle=None,
            fields=[FieldSpec(key="val", label="Value", type="text", default="x")],
        )

    def apply(self, config, values):
        self.applied = True
        return config


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_immediate_save():
    """show_section_menu returning 'save_and_exit' immediately ends collect()."""
    backend = _ScriptedBackend(["save_and_exit"])

    import gently
    original_forms = gently.FORMS
    gently.FORMS = [_CompleteForm()]
    try:
        config, action = collect(GentlyConfig(), backend)
    finally:
        gently.FORMS = original_forms

    assert action == "save_and_exit"
    assert len(backend.menu_calls) == 1
    assert len(backend.form_calls) == 0
    print("PASS  immediate save_and_exit terminates collect()")


def test_immediate_install():
    """show_section_menu returning 'install' ends collect() with install action."""
    backend = _ScriptedBackend(["install"])

    import gently
    original_forms = gently.FORMS
    gently.FORMS = [_CompleteForm()]
    try:
        config, action = collect(GentlyConfig(), backend)
    finally:
        gently.FORMS = original_forms

    assert action == "install"
    assert len(backend.menu_calls) == 1
    print("PASS  install action propagated from menu")


def test_edit_then_save():
    """Selecting a section runs its form; returning to menu and saving works."""
    form = _IncompleteForm("alpha")
    backend = _ScriptedBackend(["edit:stage3", "save_and_exit"])

    import gently
    original_forms = gently.FORMS
    gently.FORMS = [form]
    try:
        config, action = collect(GentlyConfig(), backend)
    finally:
        gently.FORMS = original_forms

    assert action == "save_and_exit"
    assert form.applied, "apply() must be called after show_form"
    assert backend.form_calls == ["Form: alpha"]
    assert len(backend.menu_calls) == 2, "menu shown before and after editing"
    print("PASS  edit section → back to menu → save")


def test_complete_status_passed_to_menu():
    """sections list passed to show_section_menu carries correct is_complete flags."""
    complete_form = _CompleteForm()
    incomplete_form = _IncompleteForm("beta")
    backend = _ScriptedBackend(["save_and_exit"])

    import gently
    original_forms = gently.FORMS
    gently.FORMS = [complete_form, incomplete_form]
    try:
        collect(GentlyConfig(), backend)
    finally:
        gently.FORMS = original_forms

    sections, all_complete = backend.menu_calls[0]
    assert sections[0][2] is True,  "CompleteForm should be marked complete"
    assert sections[1][2] is False, "IncompleteForm should be marked incomplete"
    assert all_complete is False,   "all_complete must be False when any section is incomplete"
    print("PASS  completion flags correctly passed to show_section_menu")


def test_all_forms_importable():
    """All 10 form stubs must be importable and have the required attributes."""
    from gently import FORMS
    for form in FORMS:
        assert hasattr(form, "section_name"), f"{type(form).__name__} missing section_name"
        assert hasattr(form, "section_key"),  f"{type(form).__name__} missing section_key"
        assert form.section_name, f"{type(form).__name__}.section_name is empty"
        assert form.section_key,  f"{type(form).__name__}.section_key is empty"
    print(f"PASS  all {len(FORMS)} form stubs importable with valid attributes")


if __name__ == "__main__":
    test_immediate_save()
    test_immediate_install()
    test_edit_then_save()
    test_complete_status_passed_to_menu()
    test_all_forms_importable()
    print()
    print("All collect() engine tests passed.")
