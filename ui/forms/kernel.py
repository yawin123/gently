from ui.forms.base import SectionForm


class KernelForm(SectionForm):
    section_name = "Kernel"
    section_key  = "kernel"

    def is_complete(self, config):
        raise NotImplementedError  # Milestone 3

    def build_form(self, config):
        raise NotImplementedError  # Milestone 3

    def apply(self, config, values):
        raise NotImplementedError  # Milestone 3
