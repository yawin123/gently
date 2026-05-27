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
        fields = [
            FieldSpec(key="arch", label="Architecture", i18n_key="form_stage3_arch_label",
                      type="choice", default=s.arch or "amd64", options=_ARCHES),
            FieldSpec(key="variant", label="Variant", i18n_key="form_stage3_variant_label",
                      type="choice", default=s.variant or "openrc", options=["openrc", "systemd"],
                      help="form_stage3_variant_help"),
            FieldSpec(key="mirror", label="Mirror URL", i18n_key="form_stage3_mirror_label",
                      type="text", default=s.mirror or "https://distfiles.gentoo.org",
                      required=False, help="form_stage3_mirror_help"),
            FieldSpec(key="local_path", label="Local tarball path", i18n_key="form_stage3_local_path_label",
                      type="text", default=s.local_path, required=False, help="form_stage3_local_path_help"),
            FieldSpec(key="tarball_url", label="Tarball URL", i18n_key="form_stage3_tarball_url_label",
                      type="text", default=s.tarball_url, required=False, help="form_stage3_tarball_url_help"),
            FieldSpec(key="verify_signature", label="Verify signature", i18n_key="form_stage3_verify_sig_label",
                      type="bool", default=s.verify_signature, required=False,
                      help="form_stage3_verify_sig_help"),
            FieldSpec(key="signature_url", label="Signature URL", i18n_key="form_stage3_sig_url_label",
                      type="text", default=s.signature_url, required=False,
                      visible_when=("verify_signature", True), help="form_stage3_sig_url_help"),
            FieldSpec(key="signature_path", label="Signature path", i18n_key="form_stage3_sig_path_label",
                      type="text", default=s.signature_path, required=False,
                      visible_when=("verify_signature", True), help="form_stage3_sig_path_help"),
        ]
        return FormSpec(
            title="form_stage3_title",
            subtitle="form_stage3_subtitle",
            fields=fields,
        )

    def apply(self, config: GentlyConfig, values: dict) -> GentlyConfig:
        config.stage3 = Stage3Config(
            arch=values.get("arch") or None,
            variant=values.get("variant") or None,
            mirror=values.get("mirror") or None,
            local_path=values.get("local_path") or None,
            tarball_url=values.get("tarball_url") or None,
            signature_url=values.get("signature_url") or None,
            signature_path=values.get("signature_path") or None,
            verify_signature=bool(values.get("verify_signature", True)),
        )
        return config
