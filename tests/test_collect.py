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

class _NoopBackend(UIBackend):
    """Backend that records calls without touching the terminal."""

    def __init__(self):
        self.info_calls = []
        self.form_calls = []

    def show_form(self, form: FormSpec) -> dict:
        self.form_calls.append(form.title)
        # Return whatever the fields' defaults are
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
        self.info_calls.append(lines[0] if lines else "")


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

def test_complete_section_is_skipped():
    """Sections where is_complete() → True must be skipped (show_info called)."""
    backend = _NoopBackend()
    form = _CompleteForm()

    import gently
    original_forms = gently.FORMS
    gently.FORMS = [form]
    try:
        collect(GentlyConfig(), backend)
    finally:
        gently.FORMS = original_forms

    assert len(backend.info_calls) == 1, "Expected one show_info call"
    assert "complete" in backend.info_calls[0].lower()
    assert len(backend.form_calls) == 0, "show_form must not be called"
    print("PASS  complete section is skipped, show_info called")


def test_incomplete_section_runs_form():
    """Sections where is_complete() → False must run the form cycle."""
    backend = _NoopBackend()
    form = _IncompleteForm("alpha")

    import gently
    original_forms = gently.FORMS
    gently.FORMS = [form]
    try:
        collect(GentlyConfig(), backend)
    finally:
        gently.FORMS = original_forms

    assert form.applied, "apply() must be called after show_form"
    assert backend.form_calls == ["Form: alpha"]
    assert len(backend.info_calls) == 0
    print("PASS  incomplete section runs form, apply() called")


def test_mixed_sections_order():
    """Complete sections are skipped; incomplete ones run, in order."""
    backend = _NoopBackend()
    f_complete   = _CompleteForm()
    f_incomplete = _IncompleteForm("beta")

    import gently
    original_forms = gently.FORMS
    gently.FORMS = [f_complete, f_incomplete]
    try:
        collect(GentlyConfig(), backend)
    finally:
        gently.FORMS = original_forms

    assert len(backend.info_calls) == 1
    assert len(backend.form_calls) == 1
    assert backend.form_calls[0] == "Form: beta"
    assert f_incomplete.applied
    print("PASS  mixed sections: complete skipped, incomplete runs, order preserved")


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
    test_complete_section_is_skipped()
    test_incomplete_section_runs_form()
    test_mixed_sections_order()
    test_all_forms_importable()
    print()
    print("All collect() engine tests passed.")
