from ui.forms.base import SectionForm


class BootloaderForm(SectionForm):
    section_name = "Bootloader"
    section_key  = "bootloader"

    def is_complete(self, config):
        raise NotImplementedError  # Milestone 3

    def build_form(self, config):
        raise NotImplementedError  # Milestone 3

    def apply(self, config, values):
        raise NotImplementedError  # Milestone 3
