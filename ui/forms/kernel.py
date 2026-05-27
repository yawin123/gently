from __future__ import annotations

from model.config import GentlyConfig, KernelConfig, KernelCustomConfig
from ui.abstract import FieldSpec, FormSpec
from ui.forms.base import SectionForm

_METHODS = ["genkernel", "dist-kernel", "menuconfig", "custom"]


class KernelForm(SectionForm):
    section_name = "Kernel"
    section_key  = "kernel"

    def is_complete(self, config: GentlyConfig) -> bool:
        return config.kernel is not None and config.kernel.method is not None

    def build_form(self, config: GentlyConfig) -> FormSpec:
        k = config.kernel or KernelConfig()
        custom = k.custom or KernelCustomConfig()
        return FormSpec(
            title="Kernel configuration",
            subtitle=None,
            fields=[
                FieldSpec(
                    key="method",
                    label="Build method",
                    type="choice",
                    default=k.method or "genkernel",
                    options=_METHODS,
                    help=(
                        "genkernel: automated  |  dist-kernel: binary  |  "
                        "menuconfig/custom: manual (config_path required)"
                    ),
                ),
                FieldSpec(
                    key="config_path",
                    label="Config file path",
                    type="text",
                    default=custom.config_path,
                    required=False,
                    help="Path to a .config file (only used with menuconfig or custom)",
                ),
                FieldSpec(
                    key="extra_modules",
                    label="Extra modules",
                    type="list",
                    default=list(k.extra_modules) if k.extra_modules else None,
                    required=False,
                    help="Additional kernel modules to force-include",
                ),
            ],
        )

    def apply(self, config: GentlyConfig, values: dict) -> GentlyConfig:
        config_path = values.get("config_path") or None
        config.kernel = KernelConfig(
            method=values.get("method") or None,
            extra_modules=values.get("extra_modules") or None,
            custom=KernelCustomConfig(config_path=config_path) if config_path else None,
        )
        return config
