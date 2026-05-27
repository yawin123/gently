from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from model.config import GentlyConfig
    from ui.abstract import UIBackend, FormSpec


class SectionForm:
    """
    Base class for all configuration section forms.

    Subclasses must define class-level attributes `section_name` and
    `section_key`, and implement `is_complete`, `build_form`, and `apply`.
    """

    section_name: str = ""   # human-readable title, e.g. "System configuration"
    section_key:  str = ""   # attribute name on GentlyConfig, e.g. "system"

    def is_complete(self, config: GentlyConfig) -> bool:
        """
        Return True if the section already has all required fields filled.
        The collect engine uses this to skip sections that need no input.
        Implemented by each subclass (Milestone 3).
        """
        raise NotImplementedError

    def build_form(self, config: GentlyConfig) -> FormSpec:
        """
        Build and return the FormSpec for this section, pre-populated with
        the current values from `config`.
        Implemented by each subclass (Milestone 3).
        """
        raise NotImplementedError

    def apply(self, config: GentlyConfig, values: dict) -> GentlyConfig:
        """
        Map the dict returned by the backend back onto `config` and return
        the updated GentlyConfig.
        Implemented by each subclass (Milestone 3).
        """
        raise NotImplementedError

    def run(self, config: GentlyConfig, backend: UIBackend) -> GentlyConfig:
        """
        Default collect cycle: build form → show → apply.
        Subclasses may override this only when the default cycle is
        insufficient (e.g. DisksForm with its dynamic list editor).
        """
        form = self.build_form(config)
        values = backend.show_form(form)
        if values is None:
            return config
        return self.apply(config, values)
