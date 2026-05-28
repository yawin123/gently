from __future__ import annotations

import re
import subprocess


def parse_size_to_bytes(size_expr: str | None) -> int | None:
    """Convert a human-readable size string (e.g. '20G', '512M') to bytes.

    Returns None for empty input, percentage expressions, or unrecognised formats.
    """
    if not size_expr:
        return None
    s = size_expr.strip()
    if not s or s.endswith("%"):
        return None
    m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*([kmgtp]?)(?:i?b)?", s, re.IGNORECASE)
    if not m:
        return None
    num = float(m.group(1))
    unit = m.group(2).upper()
    multiplier = {
        "": 1,
        "K": 1024,
        "M": 1024 ** 2,
        "G": 1024 ** 3,
        "T": 1024 ** 4,
        "P": 1024 ** 5,
    }[unit]
    return int(num * multiplier)


def disk_size_bytes(device_path: str | None) -> int | None:
    """Return the size in bytes of a block device, or None on failure."""
    if not device_path:
        return None
    try:
        out = subprocess.run(
            ["lsblk", "-b", "-n", "-o", "SIZE", device_path],
            capture_output=True, text=True, timeout=5,
        )
        lines = out.stdout.strip().splitlines()
        if not lines:
            return None
        return int(lines[0].strip())
    except Exception:
        return None
