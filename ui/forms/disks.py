from ui.forms.base import SectionForm


class DisksForm(SectionForm):
    section_name = "Disk layout"
    section_key  = "disks"

    def is_complete(self, config):
        raise NotImplementedError  # Milestone 3

    def build_form(self, config):
        raise NotImplementedError  # Milestone 3

    def apply(self, config, values):
        raise NotImplementedError  # Milestone 3

    # DisksForm.run() will be overridden in Milestone 3 to orchestrate
    # a dynamic list editor instead of the default form cycle.
