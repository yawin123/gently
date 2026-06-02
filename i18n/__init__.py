"""
Gently i18n module.

Loads CSV translation files and provides a t() function for lookup.
Detects system locale on import and falls back to en_US.
"""

from __future__ import annotations

import csv
import locale
import os


_CSV_DIR = os.path.dirname(os.path.abspath(__file__))
_translations: dict[str, str] = {}
_loaded: bool = False
_current_lang: str = "en-us"


def _detect_lang() -> str:
    try:
        raw, _ = locale.getlocale(locale.LC_MESSAGES)
        if raw and raw.lower() != "c":
            return raw.lower().replace("_", "-")
    except Exception:
        pass

    for var in ("LANG", "LC_ALL", "LC_MESSAGES"):
        val = os.environ.get(var)
        if val and not val.startswith("C") and not val.startswith("POSIX"):
            # Strip encoding suffix if present: es_ES.UTF-8 -> es_ES
            base = val.split(".")[0]
            parts = base.split("_", 1) if "_" in base else (base, None)
            lang_tag = parts[0].lower()
            if len(parts) > 1 and parts[1]:
                lang_tag = f"{lang_tag}-{parts[1].lower()}"
            return lang_tag

    return "en-us"


def _csv_path(lang_tag: str) -> str:
    return os.path.join(_CSV_DIR, f"{lang_tag}.csv")


def _load_csv(path: str) -> dict[str, str]:
    result: dict[str, str] = {}
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        header = next(reader, None)
        if not header:
            return result
        # Second column (index 1) is the translated text regardless of header name.
        text_idx = 1 if len(header) > 1 else None
        if text_idx is None:
            return result
        for row in reader:
            if not row or not row[0] or row[0].startswith("#"):
                continue
            result[row[0]] = row[text_idx] if len(row) > text_idx else ""
    return result


def _init() -> None:
    global _translations, _loaded, _current_lang
    if _loaded:
        return
    _loaded = True

    lang = _detect_lang()
    _current_lang = _load_best(lang)


def _load_best(lang: str) -> str:
    global _translations
    # Normalise to lowercase so es-ES matches es-es.csv.
    lang = lang.lower()
    candidates = [lang]
    if "-" in lang:
        candidates.append(lang.split("-")[0])
    for candidate in candidates:
        path = _csv_path(candidate)
        if os.path.isfile(path):
            _translations = _load_csv(path)
            return candidate

    fallback = _csv_path("en-us")
    if os.path.isfile(fallback):
        _translations = _load_csv(fallback)
    return "en-us"


def available_languages() -> list[str]:
    lang_list: list[str] = []
    for name in os.listdir(_CSV_DIR):
        if name.endswith(".csv") and not name.startswith("."):
            lang_list.append(name.replace(".csv", ""))
    lang_list.sort()
    if not lang_list:
        lang_list.append("en-us")
    return lang_list


def current_language() -> str:
    _init()
    return _current_lang


def reload(lang_tag: str | None = None) -> str:
    global _current_lang, _loaded
    if lang_tag is None:
        lang_tag = _detect_lang()
    _current_lang = _load_best(lang_tag)
    _loaded = True
    return _current_lang


def t(msg_id: str, **kwargs: object) -> str:
    _init()
    template = _translations.get(msg_id, msg_id)
    if kwargs:
        return template.format(**kwargs)
    return template
