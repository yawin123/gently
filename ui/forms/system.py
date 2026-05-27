from __future__ import annotations

import os
import subprocess

from model.config import GentlyConfig, SystemConfig
from ui.abstract import FieldSpec, FormSpec
from ui.forms.base import SectionForm

_ZONEINFO_DIR = "/usr/share/zoneinfo"
_SKIP_PREFIXES = ("posix/", "right/", "Etc/")


def _load_timezones() -> list[str]:
    """Read available timezones from the system zoneinfo directory."""
    result: list[str] = ["UTC"]
    if not os.path.isdir(_ZONEINFO_DIR):
        return result
    for region in sorted(os.listdir(_ZONEINFO_DIR)):
        region_path = os.path.join(_ZONEINFO_DIR, region)
        if not os.path.isdir(region_path):
            continue
        skip = any(region.startswith(p.rstrip("/")) for p in _SKIP_PREFIXES)
        if skip:
            continue
        for city in sorted(os.listdir(region_path)):
            city_path = os.path.join(region_path, city)
            if os.path.isfile(city_path):
                result.append(f"{region}/{city}")
            elif os.path.isdir(city_path):
                # Sub-regions like America/Argentina/
                for sub in sorted(os.listdir(city_path)):
                    if os.path.isfile(os.path.join(city_path, sub)):
                        result.append(f"{region}/{city}/{sub}")
    return result


_TIMEZONES = _load_timezones()


def _load_locales() -> list[str]:
    """Read available UTF-8 locales from /usr/share/i18n/SUPPORTED."""
    result: list[str] = []
    supported = "/usr/share/i18n/SUPPORTED"
    if os.path.isfile(supported):
        with open(supported) as f:
            for line in f:
                line = line.strip()
                if line.startswith("#") or not line:
                    continue
                parts = line.split()
                if parts and parts[0].endswith(".UTF-8"):
                    result.append(parts[0])
    if not result:
        try:
            out = subprocess.run(["locale", "-a"], capture_output=True, text=True, timeout=5)
            for loc in out.stdout.strip().splitlines():
                loc = loc.strip()
                if loc.endswith(".utf8"):
                    loc = loc[:-5] + ".UTF-8"
                if loc.endswith(".UTF-8"):
                    result.append(loc)
        except Exception:
            pass
    if not result:
        result = ["C.UTF-8", "en_US.UTF-8", "es_ES.UTF-8", "de_DE.UTF-8",
                  "fr_FR.UTF-8", "it_IT.UTF-8", "pt_PT.UTF-8"]
    return sorted(set(result))


def _load_keymaps() -> list[str]:
    """Return available console keymaps."""
    try:
        out = subprocess.run(["localectl", "list-keymaps"],
                             capture_output=True, text=True, timeout=5)
        if out.returncode == 0:
            keymaps = [l.strip() for l in out.stdout.strip().splitlines() if l.strip()]
            if keymaps:
                return keymaps
    except Exception:
        pass
    try:
        out = subprocess.run(
            ["find", "/usr/share/keymaps", "-name", "*.map.gz"],
            capture_output=True, text=True, timeout=10)
        if out.returncode == 0:
            keymaps = sorted({
                os.path.basename(p).replace(".map.gz", "")
                for p in out.stdout.strip().splitlines() if p.strip()
            })
            if keymaps:
                return keymaps
    except Exception:
        pass
    return ["us", "uk", "de", "de-latin1", "es", "fr", "fr-latin1",
            "it", "pt-latin1", "ru", "pl", "colemak", "dvorak"]


_LOCALES  = _load_locales()
_KEYMAPS  = _load_keymaps()

_REQUIRED = ("hostname", "timezone", "locale", "keymap", "lang")


class SystemForm(SectionForm):
    section_name = "System configuration"
    section_key  = "system"

    def is_complete(self, config: GentlyConfig) -> bool:
        s = config.system
        return s is not None and all(getattr(s, f) is not None for f in _REQUIRED)

    def build_form(self, config: GentlyConfig) -> FormSpec:
        s = config.system or SystemConfig()
        return FormSpec(
            title="form_system_title",
            subtitle="form_system_subtitle",
            fields=[
                FieldSpec(
                    key="hostname",
                    label="Hostname",
                    i18n_key="form_system_hostname_label",
                    type="text",
                    default=s.hostname,
                    help="form_system_hostname_help",
                ),
                FieldSpec(
                    key="timezone",
                    label="Timezone",
                    i18n_key="form_system_timezone_label",
                    type="choice",
                    default=s.timezone or "UTC",
                    options=_TIMEZONES,
                    help="form_system_timezone_help",
                ),
                FieldSpec(
                    key="locale",
                    label="Locale",
                    i18n_key="form_system_locale_label",
                    type="choice",
                    default=s.locale or "en_US.UTF-8",
                    options=_LOCALES,
                    help="form_system_locale_help",
                ),
                FieldSpec(
                    key="locales",
                    label="Extra locales",
                    i18n_key="form_system_extra_locales_label",
                    type="list",
                    default=list(s.locales) if s.locales else None,
                    options=_LOCALES,
                    required=False,
                    help="form_system_extra_locales_help",
                ),
                FieldSpec(
                    key="keymap",
                    label="Keymap",
                    i18n_key="form_system_keymap_label",
                    type="choice",
                    default=s.keymap or "us",
                    options=_KEYMAPS,
                    help="form_system_keymap_help",
                ),
                FieldSpec(
                    key="lang",
                    label="LANG",
                    i18n_key="form_system_lang_label",
                    type="choice",
                    default=s.lang or "en_US.UTF-8",
                    options=_LOCALES,
                    help="form_system_lang_help",
                ),
            ],
        )

    def apply(self, config: GentlyConfig, values: dict) -> GentlyConfig:
        config.system = SystemConfig(
            hostname=values.get("hostname") or None,
            timezone=values.get("timezone") or None,
            locale=values.get("locale") or None,
            locales=values.get("locales") or None,
            keymap=values.get("keymap") or None,
            lang=values.get("lang") or None,
        )
        return config
