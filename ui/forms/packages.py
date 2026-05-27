from __future__ import annotations

from model.config import GentlyConfig, PackagesConfig
from ui.abstract import FieldSpec, FormSpec
from ui.forms.base import SectionForm


class PackagesForm(SectionForm):
    section_name = "Extra packages"
    section_key  = "packages"

    def is_complete(self, config: GentlyConfig) -> bool:
        # This section is always considered complete; extra may be empty.
        return True

    def build_form(self, config: GentlyConfig) -> FormSpec:
        pkg = config.packages or PackagesConfig()
        return FormSpec(
            title="form_packages_title",
            subtitle="form_packages_subtitle",
            fields=[
                FieldSpec(key="extra", label="Extra packages", i18n_key="form_packages_extra_label",
                          type="list", default=list(pkg.extra) if pkg.extra else [],
                          required=False, help="form_packages_extra_help"),
                FieldSpec(key="source_only", label="Source-only packages",
                          i18n_key="form_packages_source_only_label",
                          type="list", default=list(pkg.source_only) if pkg.source_only else None,
                          required=False, help="form_packages_source_only_help"),
            ],
        )

    def apply(self, config: GentlyConfig, values: dict) -> GentlyConfig:
        config.packages = PackagesConfig(
            extra=values.get("extra") or [],
            source_only=values.get("source_only") or None,
        )
        return config
