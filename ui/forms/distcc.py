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
                FieldSpec(key="_advanced_sep", label="form_distcc_sep_advanced",
                          i18n_key="form_distcc_sep_advanced", type="separator", required=False),
                FieldSpec(key="port", label="distccd port", i18n_key="form_distcc_port_label",
                          type="int", default=d.port, required=False,
                          help="form_distcc_port_help"),
                FieldSpec(key="distcc_dir", label="distcc working dir", i18n_key="form_distcc_dir_label",
                          type="text", default=d.distcc_dir, required=False,
                          help="form_distcc_dir_help"),
            ],
        )

    def apply(self, config: GentlyConfig, values: dict) -> GentlyConfig:
        config.distcc = DistccConfig(
            enabled=bool(values.get("enabled", False)),
            hosts=values.get("hosts") or None,
            makeopts_jobs=values.get("makeopts_jobs"),
            pump_mode=bool(values.get("pump_mode", False)),
            install_on_target=bool(values.get("install_on_target", False)),
            port=values.get("port"),
            distcc_dir=values.get("distcc_dir") or None,
        )
        return config
