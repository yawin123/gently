from __future__ import annotations


def parse_int(text: str, label: str, exc_class: type[Exception] = ValueError) -> int:
    value = text.strip()
    if not value:
        raise exc_class(f"{label} returned empty output")
    try:
        return int(value)
    except ValueError as exc:
        raise exc_class(f"Could not parse integer from {label}: {value!r}") from exc
