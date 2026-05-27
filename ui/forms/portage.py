from __future__ import annotations

import re
import subprocess

from model.config import GentlyConfig, PortageConfig, PortageProfileConfig
from ui.abstract import FieldSpec, FormSpec
from ui.forms.base import SectionForm

_REQUIRED = ("cflags", "makeopts", "accept_keywords", "accept_license")

_ACCEPT_LICENSES = [
    "@FREE",
    "@BINARY-REDISTRIBUTABLE",
    "@OSI-APPROVED",
    "@FSF-APPROVED",
    "@GPL-COMPATIBLE",
    "@LGPL-2.1-compatible",
    "*",
]

_PROFILES_FALLBACK = [
    "default/linux/amd64/23.0",
    "default/linux/amd64/23.0/desktop",
    "default/linux/amd64/23.0/desktop/gnome",
    "default/linux/amd64/23.0/desktop/kde",
    "default/linux/amd64/23.0/systemd",
    "default/linux/amd64/23.0/systemd/desktop",
    "default/linux/amd64/23.0/systemd/desktop/gnome",
    "default/linux/amd64/23.0/systemd/desktop/kde",
    "default/linux/amd64/23.0/no-multilib",
    "default/linux/amd64/23.0/hardened",
    "default/linux/arm64/23.0",
    "default/linux/arm64/23.0/desktop",
    "default/linux/arm64/23.0/systemd",
]


def _load_profiles() -> list[str]:
    """Load available Portage profiles via eselect, with static fallback."""
    try:
        out = subprocess.run(
            ["eselect", "profile", "list"],
            capture_output=True, text=True, timeout=10,
        )
        if out.returncode == 0:
            profiles = [
                m.group(1)
                for line in out.stdout.splitlines()
                if (m := re.search(r"\[\d+\]\s+(\S+)", line))
            ]
            if profiles:
                return profiles
    except Exception:
        pass
    return _PROFILES_FALLBACK


_PROFILES = _load_profiles()


class PortageForm(SectionForm):
    section_name = "Portage configuration"
    section_key  = "portage"

    def is_complete(self, config: GentlyConfig) -> bool:
        p = config.portage
        if p is None:
            return False
        if not all(getattr(p, f) is not None for f in _REQUIRED):
            return False
        if p.use is None:              # must be set (may be [])
            return False
        return p.profile is not None and p.profile.name is not None

    def build_form(self, config: GentlyConfig) -> FormSpec:
        p       = config.portage or PortageConfig()
        profile = p.profile or PortageProfileConfig()
        return FormSpec(
            title="Portage configuration",
            subtitle="Compiler flags, USE flags and profile",
            fields=[
                FieldSpec(
                    key="cflags",
                    label="CFLAGS",
                    type="text",
                    default=p.cflags or "-march=native -O2 -pipe",
                    help="GCC CFLAGS, e.g. -march=native -O2 -pipe",
                ),
                FieldSpec(
                    key="cxxflags",
                    label="CXXFLAGS",
                    type="text",
                    default=p.cxxflags or p.cflags or "-march=native -O2 -pipe",
                    required=False,
                    help="Leave blank to mirror CFLAGS automatically",
                ),
                FieldSpec(
                    key="makeopts",
                    label="MAKEOPTS",
                    type="text",
                    default=p.makeopts or "-j4",
                    help="Parallel jobs, e.g. -j4 or -j$(nproc)",
                ),
                FieldSpec(
                    key="cpu_flags",
                    label="CPU_FLAGS_X86",
                    type="list",
                    default=list(p.cpu_flags) if p.cpu_flags else None,
                    required=False,
                    help="Run cpuid2cpuflags to get the correct value",
                ),
                FieldSpec(
                    key="use",
                    label="USE flags",
                    type="list",
                    default=list(p.use) if p.use is not None else [],
                    help="Global USE flags — confirm with empty list to set none",
                ),
                FieldSpec(
                    key="accept_keywords",
                    label="ACCEPT_KEYWORDS",
                    type="text",
                    default=p.accept_keywords or "amd64",
                    help="e.g. amd64 or ~amd64",
                ),
                FieldSpec(
                    key="accept_license",
                    label="ACCEPT_LICENSE",
                    type="choice",
                    default=p.accept_license or "@FREE",
                    options=_ACCEPT_LICENSES,
                    help="License group. @FREE = only free software",
                ),
                FieldSpec(
                    key="video_cards",
                    label="VIDEO_CARDS",
                    type="list",
                    default=list(p.video_cards) if p.video_cards else None,
                    required=False,
                    help="e.g. intel nouveau amdgpu",
                ),
                FieldSpec(
                    key="input_devices",
                    label="INPUT_DEVICES",
                    type="list",
                    default=list(p.input_devices) if p.input_devices else None,
                    required=False,
                ),
                FieldSpec(
                    key="mirrors",
                    label="GENTOO_MIRRORS",
                    type="list",
                    default=list(p.mirrors) if p.mirrors else None,
                    required=False,
                    help="Mirror URLs, e.g. https://mirror.leaseweb.com/gentoo/",
                ),
                FieldSpec(
                    key="profile_name",
                    label="Profile",
                    type="choice",
                    default=profile.name or "default/linux/amd64/23.0",
                    options=_PROFILES,
                    help="Active Portage profile",
                ),
            ],
        )

    def apply(self, config: GentlyConfig, values: dict) -> GentlyConfig:
        profile_name = values.get("profile_name") or None
        config.portage = PortageConfig(
            cflags=values.get("cflags") or None,
            cxxflags=values.get("cxxflags") or None,
            makeopts=values.get("makeopts") or None,
            cpu_flags=values.get("cpu_flags") or None,
            use=values.get("use"),          # keep [] as [] (valid empty USE)
            accept_keywords=values.get("accept_keywords") or None,
            accept_license=values.get("accept_license") or None,
            video_cards=values.get("video_cards") or None,
            input_devices=values.get("input_devices") or None,
            mirrors=values.get("mirrors") or None,
            profile=PortageProfileConfig(name=profile_name) if profile_name else None,
        )
        return config
