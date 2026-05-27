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
            title="form_kernel_title",
            subtitle=None,
            fields=[
                FieldSpec(key="method", label="Build method", i18n_key="form_kernel_method_label",
                          type="choice", default=k.method or "genkernel", options=_METHODS,
                          help="form_kernel_method_help"),
                FieldSpec(key="config_path", label="Config file path", i18n_key="form_kernel_config_path_label",
                          type="text", default=custom.config_path, required=False,
                          help="form_kernel_config_path_help"),
                FieldSpec(key="extra_modules", label="Extra modules", i18n_key="form_kernel_extra_modules_label",
                          type="list", default=list(k.extra_modules) if k.extra_modules else None,
                          required=False, help="form_kernel_extra_modules_help"),
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
