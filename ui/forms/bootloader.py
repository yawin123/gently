from __future__ import annotations

from model.config import BootloaderConfig, BootloaderGrubConfig, GentlyConfig
from ui.abstract import FieldSpec, FormSpec
from ui.forms.base import SectionForm

_TYPES = ["grub", "none"]


class BootloaderForm(SectionForm):
    section_name = "Bootloader"
    section_key  = "bootloader"

    def is_complete(self, config: GentlyConfig) -> bool:
        b = config.bootloader
        if b is None or not b.type:
            return False
        if b.type == "grub" and b.grub is None:
            return False
        return True

    def build_form(self, config: GentlyConfig) -> FormSpec:
        b = config.bootloader or BootloaderConfig()
        grub = b.grub or BootloaderGrubConfig()
        return FormSpec(
            title="form_bootloader_title",
            subtitle="form_bootloader_subtitle",
            fields=[
                FieldSpec(key="type", label="Bootloader type", i18n_key="form_bootloader_type_label",
                          type="choice", default=b.type or "grub", options=_TYPES,
                          help="form_bootloader_type_help"),
                FieldSpec(key="grub_install_disk", label="GRUB install disk",
                          i18n_key="form_bootloader_grub_install_disk_label",
                          type="text", default=grub.install_disk, required=False,
                          help="form_bootloader_grub_install_disk_help"),
                FieldSpec(key="grub_timeout", label="GRUB timeout (s)",
                          i18n_key="form_bootloader_grub_timeout_label",
                          type="int", default=grub.timeout if grub.timeout is not None else 5,
                          required=False),
                FieldSpec(key="grub_cmdline_extra", label="Extra kernel params",
                          i18n_key="form_bootloader_extra_params_label",
                          type="list", default=list(grub.cmdline_extra) if grub.cmdline_extra else None,
                          required=False, help="form_bootloader_extra_params_help"),
            ],
        )

    def apply(self, config: GentlyConfig, values: dict) -> GentlyConfig:
        btype = values.get("type") or "grub"
        grub = None
        if btype == "grub":
            grub = BootloaderGrubConfig(
                install_disk=values.get("grub_install_disk") or None,
                timeout=values.get("grub_timeout"),
                cmdline_extra=values.get("grub_cmdline_extra") or None,
            )
        config.bootloader = BootloaderConfig(type=btype, grub=grub)
        return config
