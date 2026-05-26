from ui.forms.base import SectionForm


class DistccForm(SectionForm):
    section_name = "distcc"
    section_key  = "distcc"

    def is_complete(self, config):
        raise NotImplementedError  # Milestone 3

    def build_form(self, config):
        raise NotImplementedError  # Milestone 3

    def apply(self, config, values):
        raise NotImplementedError  # Milestone 3
