from __future__ import annotations

from model.config import DistccConfig, GentlyConfig
from ui.abstract import FieldSpec, FormSpec
from ui.forms.base import SectionForm


class DistccForm(SectionForm):
    section_name = "distcc"
    section_key  = "distcc"

    def is_complete(self, config: GentlyConfig) -> bool:
        d = config.distcc
        if d is None or not d.enabled:
            return True          # disabled or absent → nothing required
        return bool(d.hosts)     # enabled → must have at least one host

    def build_form(self, config: GentlyConfig) -> FormSpec:
        d = config.distcc or DistccConfig()
        return FormSpec(
            title="form_distcc_title",
            subtitle="form_distcc_subtitle",
            fields=[
                FieldSpec(key="enabled", label="Enable distcc", i18n_key="form_distcc_enabled_label",
                          type="bool", default=d.enabled, required=False),
                FieldSpec(key="hosts", label="Hosts", i18n_key="form_distcc_hosts_label",
                          type="list", default=list(d.hosts) if d.hosts else None,
                          required=False, help="form_distcc_hosts_help"),
                FieldSpec(key="makeopts_jobs", label="MAKEOPTS jobs",
                          i18n_key="form_distcc_makeopts_jobs_label",
                          type="int", default=d.makeopts_jobs, required=False,
                          help="form_distcc_makeopts_jobs_help"),
                FieldSpec(key="pump_mode", label="Pump mode", i18n_key="form_distcc_pump_mode_label",
                          type="bool", default=d.pump_mode, required=False,
                          help="form_distcc_pump_mode_help"),
                FieldSpec(key="install_on_target", label="Install distcc on target",
                          i18n_key="form_distcc_install_on_target_label",
                          type="bool", default=d.install_on_target, required=False),
            ],
        )

    def apply(self, config: GentlyConfig, values: dict) -> GentlyConfig:
        config.distcc = DistccConfig(
            enabled=bool(values.get("enabled", False)),
            hosts=values.get("hosts") or None,
            makeopts_jobs=values.get("makeopts_jobs"),
            pump_mode=bool(values.get("pump_mode", False)),
            install_on_target=bool(values.get("install_on_target", False)),
        )
        return config
