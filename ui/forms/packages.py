from ui.forms.base import SectionForm


class PackagesForm(SectionForm):
    section_name = "Extra packages"
    section_key  = "packages"

    def is_complete(self, config):
        raise NotImplementedError  # Milestone 3

    def build_form(self, config):
        raise NotImplementedError  # Milestone 3

    def apply(self, config, values):
        raise NotImplementedError  # Milestone 3
