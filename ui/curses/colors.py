"""Curses color setup and low-level helpers."""

import curses


# ---------------------------------------------------------------------------
# Color pair IDs (module-level constants)
# ---------------------------------------------------------------------------

_P_NORMAL = 1   # default text
_P_ACTIVE = 2   # highlighted / selected
_P_TITLE  = 3   # title bar
_P_STATUS = 4   # status bar
_P_ERROR  = 5   # error popup title


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

def setup_colors() -> None:
    """Initialise curses colour pairs using the default terminal background."""
    if curses.has_colors():
        curses.start_color()
        curses.use_default_colors()
        curses.init_pair(_P_NORMAL, -1, -1)
        curses.init_pair(_P_ACTIVE, curses.COLOR_BLACK, curses.COLOR_CYAN)
        curses.init_pair(_P_TITLE,  curses.COLOR_CYAN,  -1)
        curses.init_pair(_P_STATUS, curses.COLOR_BLACK, curses.COLOR_WHITE)
        curses.init_pair(_P_ERROR,  curses.COLOR_WHITE, curses.COLOR_RED)


def safe_write(win: curses.window, y: int, x: int, text: str, attr: int = 0) -> None:
    """``addstr`` that silently ignores writes beyond terminal boundaries."""
    try:
        if attr:
            win.addstr(y, x, text, attr)
        else:
            win.addstr(y, x, text)
    except curses.error:
        pass
