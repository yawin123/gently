from __future__ import annotations

from model.config import GentlyConfig, Stage3Config
from ui.abstract import FieldSpec, FormSpec
from ui.forms.base import SectionForm

_ARCHES = ["amd64", "arm64", "x86", "arm", "ppc64le", "riscv"]


class Stage3Form(SectionForm):
    section_name = "Stage3 tarball"
    section_key  = "stage3"

    def is_complete(self, config: GentlyConfig) -> bool:
        s = config.stage3
        if s is None or not s.arch or not s.variant:
            return False
        return bool(s.local_path or s.tarball_url or s.mirror)

    def build_form(self, config: GentlyConfig) -> FormSpec:
        s = config.stage3 or Stage3Config()
        return FormSpec(
            title="Stage3 tarball",
            subtitle="Architecture, variant and source for the base system tarball",
            fields=[
                FieldSpec(
                    key="arch",
                    label="Architecture",
                    type="choice",
                    default=s.arch or "amd64",
                    options=_ARCHES,
                ),
                FieldSpec(
                    key="variant",
                    label="Variant",
                    type="choice",
                    default=s.variant or "openrc",
                    options=["openrc", "systemd"],
                    help="Init system bundled in the stage3 tarball",
                ),
                FieldSpec(
                    key="mirror",
                    label="Mirror URL",
                    type="text",
                    default=s.mirror,
                    required=False,
                    help="Distfiles mirror — Gently fetches the latest tarball automatically",
                ),
                FieldSpec(
                    key="local_path",
                    label="Local tarball path",
                    type="text",
                    default=s.local_path,
                    required=False,
                    help="Path to a locally downloaded stage3 tarball",
                ),
                FieldSpec(
                    key="tarball_url",
                    label="Tarball URL",
                    type="text",
                    default=s.tarball_url,
                    required=False,
                    help="Direct URL to a specific stage3 tarball",
                ),
                FieldSpec(
                    key="verify_signature",
                    label="Verify signature",
                    type="bool",
                    default=s.verify_signature,
                    required=False,
                ),
            ],
        )

    def apply(self, config: GentlyConfig, values: dict) -> GentlyConfig:
        config.stage3 = Stage3Config(
            arch=values.get("arch") or None,
            variant=values.get("variant") or None,
            mirror=values.get("mirror") or None,
            local_path=values.get("local_path") or None,
            tarball_url=values.get("tarball_url") or None,
            verify_signature=bool(values.get("verify_signature", True)),
        )
        return config
