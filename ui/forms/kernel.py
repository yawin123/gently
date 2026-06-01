from __future__ import annotations

from model.config import GentlyConfig, KernelConfig, KernelCustomConfig
from ui.abstract import FieldSpec, FormSpec
from ui.forms.base import SectionForm

_METHODS = ["installkernel", "menuconfig", "custom"]


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
            subtitle="form_kernel_subtitle",
            fields=[
                FieldSpec(key="method", label="Build method", i18n_key="form_kernel_method_label",
                          type="choice", default=k.method or "installkernel", options=_METHODS,
                          help="form_kernel_method_help"),
                FieldSpec(key="binary", label="Use binary packages", i18n_key="form_kernel_binary_label",
                          type="bool", default=k.binary if k.binary is not None else True,
                          visible_when=("method", "installkernel"),
                          help="form_kernel_binary_help"),
                FieldSpec(key="config_path", label="Config file path", i18n_key="form_kernel_config_path_label",
                          type="text", default=custom.config_path, required=False,
                          visible_when=("method", ("menuconfig", "custom")),
                          help="form_kernel_config_path_help"),
                FieldSpec(key="extra_modules", label="Extra modules", i18n_key="form_kernel_extra_modules_label",
                          type="list", default=list(k.extra_modules) if k.extra_modules else None,
                          required=False, help="form_kernel_extra_modules_help"),
                FieldSpec(key="_firmware_sep", label="form_kernel_firmware_sep",
                          i18n_key="form_kernel_firmware_sep",
                          type="separator", required=False),
                FieldSpec(key="linux_firmware", label="linux-firmware", i18n_key="form_kernel_linux_firmware_label",
                          type="bool", default=k.linux_firmware if k.linux_firmware is not None else False,
                          required=False, help="form_kernel_linux_firmware_help"),
                FieldSpec(key="intel_microcode", label="intel-microcode", i18n_key="form_kernel_intel_microcode_label",
                          type="bool", default=k.intel_microcode if k.intel_microcode is not None else False,
                          required=False, help="form_kernel_intel_microcode_help"),
                FieldSpec(key="sof_firmware", label="sof-firmware", i18n_key="form_kernel_sof_firmware_label",
                          type="bool", default=k.sof_firmware if k.sof_firmware is not None else False,
                          required=False, help="form_kernel_sof_firmware_help"),
            ],
        )

    def apply(self, config: GentlyConfig, values: dict) -> GentlyConfig:
        config_path = values.get("config_path") or None
        method = values.get("method") or None
        binary = values.get("binary")
        if binary is None and method == "installkernel":
            binary = True
        config.kernel = KernelConfig(
            method=method,
            binary=binary if method == "installkernel" else None,
            extra_modules=values.get("extra_modules") or None,
            linux_firmware=values.get("linux_firmware") or None,
            intel_microcode=values.get("intel_microcode") or None,
            sof_firmware=values.get("sof_firmware") or None,
            custom=KernelCustomConfig(config_path=config_path) if config_path else None,
        )
        return config
