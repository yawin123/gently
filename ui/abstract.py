from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class FieldSpec:
    key:      str                    # internal identifier
    label:    str                    # visible label
    type:     str                    # "text" | "password" | "choice" | "bool" | "list" | "int"
    default:  Any                    = None
    options:  list[str] | None       = field(default=None)   # for type="choice"
    help:     str | None             = field(default=None)   # shown on '?'
    required: bool                   = True


@dataclass
class FormSpec:
    title:    str
    subtitle: str | None
    fields:   list[FieldSpec]


class UIBackend(ABC):

    @abstractmethod
    def show_form(self, form: FormSpec) -> dict[str, Any]:
        """
        Display the form and return a dict of values keyed by FieldSpec.key.
        Fields that were not modified return their default value.
        """

    @abstractmethod
    def show_summary(self, sections: list[tuple[str, dict]]) -> str:
        """
        Display the full configuration summary.
        Returns the action chosen by the user:
          "install" | "edit:<section_key>" | "save_and_exit"
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
