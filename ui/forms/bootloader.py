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
            title="Bootloader",
            subtitle="GRUB fields are only used when type=grub",
            fields=[
                FieldSpec(
                    key="type",
                    label="Bootloader type",
                    type="choice",
                    default=b.type or "grub",
                    options=_TYPES,
                    help="grub: install GRUB2  |  none: skip bootloader installation",
                ),
                FieldSpec(
                    key="grub_install_disk",
                    label="GRUB install disk",
                    type="text",
                    default=grub.install_disk,
                    required=False,
                    help="Disk to install GRUB to, e.g. /dev/sda (blank = auto-detect)",
                ),
                FieldSpec(
                    key="grub_timeout",
                    label="GRUB timeout (s)",
                    type="int",
                    default=grub.timeout if grub.timeout is not None else 5,
                    required=False,
                ),
                FieldSpec(
                    key="grub_cmdline_extra",
                    label="Extra kernel params",
                    type="list",
                    default=list(grub.cmdline_extra) if grub.cmdline_extra else None,
                    required=False,
                    help="Extra parameters appended to the kernel command line",
                ),
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
