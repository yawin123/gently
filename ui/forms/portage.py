from ui.forms.base import SectionForm


class PortageForm(SectionForm):
    section_name = "Portage configuration"
    section_key  = "portage"

    def is_complete(self, config):
        raise NotImplementedError  # Milestone 3

    def build_form(self, config):
        raise NotImplementedError  # Milestone 3

    def apply(self, config, values):
        raise NotImplementedError  # Milestone 3
