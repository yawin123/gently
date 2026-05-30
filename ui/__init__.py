"""UI backend factory.

Usage::

    from ui import create_backend
    backend = create_backend()          # uses $GENTLY_UI or "curses"
    backend = create_backend("curses")  # explicit backend name
"""

from __future__ import annotations

import os

from ui.abstract import UIBackend


def create_backend(name: str | None = None) -> UIBackend:
    """Return a UI backend instance.

    *name* selects the backend; ``None`` (the default) reads the
    ``GENTLY_UI`` environment variable, falling back to ``"curses"``.

    Currently supported values:
      ``"curses"`` — ncurses-based terminal UI
    """
    if name is None:
        name = os.environ.get("GENTLY_UI", "curses")

    if name == "curses":
        from ui.curses import CursesBackend
        return CursesBackend()

    raise ValueError(
        f"Unknown UI backend: {name!r}. "
        f"Valid options: curses"
    )
