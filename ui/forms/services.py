from ui.forms.base import SectionForm


class ServicesForm(SectionForm):
    section_name = "Services"
    section_key  = "services"

    def is_complete(self, config):
        raise NotImplementedError  # Milestone 3

    def build_form(self, config):
        raise NotImplementedError  # Milestone 3

    def apply(self, config, values):
        raise NotImplementedError  # Milestone 3
