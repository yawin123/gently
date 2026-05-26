from ui.forms.base import SectionForm


class SystemForm(SectionForm):
    section_name = "System configuration"
    section_key  = "system"

    def is_complete(self, config):
        raise NotImplementedError  # Milestone 3

    def build_form(self, config):
        raise NotImplementedError  # Milestone 3

    def apply(self, config, values):
        raise NotImplementedError  # Milestone 3
