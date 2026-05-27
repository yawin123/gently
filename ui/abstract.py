from __future__ import annotations

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

    @abstractmethod
    def show_error(self, message: str) -> None:
        """Display a blocking error dialog."""

    @abstractmethod
    def show_confirm(self, message: str) -> bool:
        """Display a yes/no confirmation dialog."""

    @abstractmethod
    def show_info(self, title: str, lines: list[str]) -> None:
        """Display non-interactive information (completed section, notice, etc.)"""
