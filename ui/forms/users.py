from ui.forms.base import SectionForm


class UsersForm(SectionForm):
    section_name = "Users"
    section_key  = "users"

    def is_complete(self, config):
        raise NotImplementedError  # Milestone 3

    def build_form(self, config):
        raise NotImplementedError  # Milestone 3

    def apply(self, config, values):
        raise NotImplementedError  # Milestone 3
