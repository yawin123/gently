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
            title="distcc (distributed compilation)",
            subtitle="Leave enabled=no to skip distcc entirely",
            fields=[
                FieldSpec(
                    key="enabled",
                    label="Enable distcc",
                    type="bool",
                    default=d.enabled,
                    required=False,
                ),
                FieldSpec(
                    key="hosts",
                    label="Hosts",
                    type="list",
                    default=list(d.hosts) if d.hosts else None,
                    required=False,
                    help="distcc host list, e.g. localhost/4 192.168.1.10/8",
                ),
                FieldSpec(
                    key="makeopts_jobs",
                    label="MAKEOPTS jobs",
                    type="int",
                    default=d.makeopts_jobs,
                    required=False,
                    help="Override number of parallel jobs for distcc builds",
                ),
                FieldSpec(
                    key="pump_mode",
                    label="Pump mode",
                    type="bool",
                    default=d.pump_mode,
                    required=False,
                    help="Use distcc-pump for header file distribution",
                ),
                FieldSpec(
                    key="install_on_target",
                    label="Install distcc on target",
                    type="bool",
                    default=d.install_on_target,
                    required=False,
                ),
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
