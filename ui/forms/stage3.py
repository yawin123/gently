from ui.forms.base import SectionForm


class Stage3Form(SectionForm):
    section_name = "Stage3 tarball"
    section_key  = "stage3"

    def is_complete(self, config):
        raise NotImplementedError  # Milestone 3

    def build_form(self, config):
        raise NotImplementedError  # Milestone 3

    def apply(self, config, values):
        raise NotImplementedError  # Milestone 3
