from __future__ import annotations

import sys
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class FieldSpec:
    key:      str                    # internal identifier
    label:    str                    # visible label
    type:     str                    # "text" | "password" | "choice" | "bool" | "list" | "int" | "subsection"
    default:  Any                    = None
    options:  list[str] | None       = field(default=None)   # for type="choice" or filtered type="list"
    help:     str | None             = field(default=None)   # shown on '?'
    required: bool                   = True
    visible_when: tuple[str, Any] | None = field(default=None)  # (field_key, value) — field only visible when condition matches
    i18n_key: str | None             = field(default=None)   # translation key for label (falls back to label)


@dataclass
class FormSpec:
    title:    str
    subtitle: str | None
    fields:   list[FieldSpec]
    actions:  list[tuple[str, str]] | None = None


class UIBackend(ABC):

    @abstractmethod
    def show_form(self, form: FormSpec) -> dict[str, Any] | None:
        """
        Display the form and return a dict of values keyed by FieldSpec.key.
        Fields that were not modified return their default value.
        Return None if the user cancels the form.
        """

    @abstractmethod
    def show_summary(self, sections: list[tuple[str, dict]]) -> str:
        """
        Display the full configuration summary.
        Returns the action chosen by the user:
                    "save_and_exit" | "edit:<section_key>"
        """

        @abstractmethod
        def show_subsection(self, title: str, items: list[tuple[str, dict[str, Any]]]) -> str:
                """
                Display an editable subsection with list-like items.
                Returns one of:
                    "add" | "done" | "edit:<index>"
                """

    @abstractmethod
    def show_progress(self, phase: str, message: str) -> None:
        """Display progress during installation (non-blocking update)."""

    # ── Full-screen installation log ───────────────────────

    def install_progress_begin(self, phase_keys: list[str]) -> None:
        """Called once before the first phase starts."""

    def install_progress_update(self, phase_key: str, message: str) -> None:
        """Append a log line for *phase_key*."""

    def install_progress_end(self, report: Any) -> None:
        """Called after the last phase finishes (or on error)."""

    @abstractmethod
    def show_error(self, message: str) -> None:
        """Display a blocking error dialog."""

    @abstractmethod
    def show_confirm(self, message: str) -> bool:
        """Display a yes/no confirmation dialog."""

    @abstractmethod
    def show_info(self, title: str, lines: list[str]) -> None:
        """Display non-interactive information (completed section, notice, etc.)"""

    def interrupt(self) -> None:
        """Called when the user presses Ctrl+C. Default: exit immediately."""
        sys.exit(0)

    # ── i18n ───────────────────────────────────────────────

    def translate(self, msg_id: str, **kwargs: object) -> str:
        """Translate a message id. Backends may override this."""
        from i18n import t as _t
        return _t(msg_id, **kwargs)

    def available_languages(self) -> list[str]:
        from i18n import available_languages
        return available_languages()

    def current_language(self) -> str:
        from i18n import current_language
        return current_language()

    def reload_language(self, lang_tag: str) -> str:
        from i18n import reload
        return reload(lang_tag)
