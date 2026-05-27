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
            title="Extra packages",
            subtitle="Additional packages to install after the base system",
            fields=[
                FieldSpec(
                    key="extra",
                    label="Extra packages",
                    type="list",
                    default=list(pkg.extra) if pkg.extra else [],
                    required=False,
                    help="Packages to install, e.g. vim git htop",
                ),
                FieldSpec(
                    key="source_only",
                    label="Source-only packages",
                    type="list",
                    default=list(pkg.source_only) if pkg.source_only else None,
                    required=False,
                    help="Packages that must always be compiled from source (no binpkg)",
                ),
            ],
        )

    def apply(self, config: GentlyConfig, values: dict) -> GentlyConfig:
        config.packages = PackagesConfig(
            extra=values.get("extra") or [],
            source_only=values.get("source_only") or None,
        )
        return config
