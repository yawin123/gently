"""Curses widgets: inline editor, choice popup, list editor, popup, confirm."""

import curses
import curses.ascii
from typing import Any

from ui.abstract import UIBackend, FieldSpec
from ui.curses.colors import _P_NORMAL, _P_ACTIVE, _P_TITLE, _P_STATUS, _P_ERROR, safe_write


# ---------------------------------------------------------------------------
# Field value formatter
# ---------------------------------------------------------------------------

def _fmt(field: FieldSpec, value: Any) -> str:
    if value is None:
        return ""
    if field.type == "password":
        return "*" * len(str(value))
    if field.type == "bool":
        return "yes" if value else "no"
    if field.type == "list":
        return ", ".join(str(v) for v in value) if value else ""
    return str(value)


# ---------------------------------------------------------------------------
# Inline text editor (text / int / password)
# ---------------------------------------------------------------------------

def inline_editor(
    stdscr: curses.window,
    row: int, col: int, width: int,
    initial: str,
    password: bool,
) -> str | None:
    """Edit a single line at (row, col) with the given display width.

    Returns the new string on Enter, None on Esc.
    """
    buf = list(initial)
    pos = len(buf)
    hoffset = 0  # horizontal scroll offset

    curses.curs_set(1)
    try:
        while True:
            display = ("*" * len(buf)) if password else "".join(buf)
            # keep cursor visible inside the display window
            if pos - hoffset >= width:
                hoffset = pos - width + 1
            elif pos < hoffset:
                hoffset = pos
            visible = display[hoffset:hoffset + width]
            safe_write(stdscr, row, col, visible.ljust(width))
            try:
                stdscr.move(row, col + (pos - hoffset))
            except curses.error:
                pass
            stdscr.refresh()

            key = stdscr.getch()
            if key in (curses.KEY_ENTER, 10, 13):
                return "".join(buf)
            elif key == 27:
                return None
            elif key in (curses.KEY_BACKSPACE, 127, curses.ascii.BS):
                if pos > 0:
                    buf.pop(pos - 1)
                    pos -= 1
            elif key == curses.KEY_DC:
                if pos < len(buf):
                    buf.pop(pos)
            elif key == curses.KEY_LEFT:
                pos = max(0, pos - 1)
            elif key == curses.KEY_RIGHT:
                pos = min(len(buf), pos + 1)
            elif key == curses.KEY_HOME:
                pos = 0
            elif key == curses.KEY_END:
                pos = len(buf)
            elif curses.ascii.isprint(key):
                buf.insert(pos, chr(key))
                pos += 1
    finally:
        curses.curs_set(0)


# ---------------------------------------------------------------------------
# Choice popup
# ---------------------------------------------------------------------------

def choice_popup(stdscr: curses.window, field: FieldSpec, current: Any, backend: UIBackend) -> Any:
    if not field.options:
        return current
    options = field.options
    try:
        sel = options.index(current) if current in options else 0
    except ValueError:
        sel = 0

    height, width = stdscr.getmaxyx()
    box_w = min(max(len(o) for o in options) + 8, width - 4)
    box_h = min(len(options) + 4, height - 4)
    visible = box_h - 4
    scroll = 0

    win = curses.newwin(box_h, max(box_w, 20), max(0, (height - box_h) // 2),
                        max(0, (width - box_w) // 2))
    win.keypad(True)

    while True:
        win.clear()
        win.box()
        title = backend.translate("ui_choice_popup_title").format(field_label=field.label)
        safe_write(win, 0, max(1, (box_w - len(title)) // 2), title, curses.A_BOLD)

        if sel < scroll:
            scroll = sel
        elif sel >= scroll + visible:
            scroll = sel - visible + 1

        for i in range(visible):
            idx = scroll + i
            if idx >= len(options):
                break
            attr = curses.color_pair(_P_ACTIVE) if idx == sel else curses.color_pair(_P_NORMAL)
            marker = "▶ " if idx == sel else "  "
            win.attron(attr)
            safe_write(win, i + 2, 1, f"{marker}{options[idx]}"[:box_w - 2])
            win.attroff(attr)

        safe_write(win, box_h - 1, 1,
                   backend.translate("ui_choice_popup_hint")[:box_w - 2],
                   curses.color_pair(_P_STATUS))
        win.refresh()

        key = win.getch()
        if key == curses.KEY_DOWN:
            sel = min(len(options) - 1, sel + 1)
        elif key == curses.KEY_UP:
            sel = max(0, sel - 1)
        elif key == curses.KEY_NPAGE:          # PageDown
            sel = min(len(options) - 1, sel + visible)
        elif key == curses.KEY_PPAGE:          # PageUp
            sel = max(0, sel - visible)
        elif key in (curses.KEY_ENTER, 10, 13):
            del win
            return options[sel]
        elif key == 27:
            del win
            return current


# ---------------------------------------------------------------------------
# List editor
# ---------------------------------------------------------------------------

def _prompt_line(stdscr: curses.window, row: int, prompt: str) -> str | None:
    _, width = stdscr.getmaxyx()
    fw = max(width - len(prompt) - 2, 5)
    safe_write(stdscr, row, 0, (prompt + " " * fw)[:width - 1])
    stdscr.refresh()
    return inline_editor(stdscr, row, len(prompt), fw, "", False)


def list_editor(stdscr: curses.window, field: FieldSpec, current: list | None, backend: UIBackend) -> list | None:
    items = list(current) if current else []
    sel = 0

    while True:
        height, width = stdscr.getmaxyx()
        stdscr.clear()

        stdscr.attron(curses.color_pair(_P_TITLE) | curses.A_BOLD)
        safe_write(stdscr, 0, 0, (" " + backend.translate("ui_list_editor_title").format(field_label=field.label)).ljust(width))
        stdscr.attroff(curses.color_pair(_P_TITLE) | curses.A_BOLD)

        for i, item in enumerate(items):
            if i + 2 >= height - 1:
                break
            attr = curses.color_pair(_P_ACTIVE) if i == sel else curses.color_pair(_P_NORMAL)
            marker = "▶ " if i == sel else "  "
            stdscr.attron(attr)
            safe_write(stdscr, i + 2, 0, f"{marker}{item}"[:width - 1])
            stdscr.attroff(attr)

        hint = backend.translate("ui_list_editor_hint")
        stdscr.attron(curses.color_pair(_P_STATUS))
        safe_write(stdscr, height - 1, 0, hint[:width - 1].ljust(width - 1))
        stdscr.attroff(curses.color_pair(_P_STATUS))
        stdscr.refresh()

        key = stdscr.getch()
        if key == curses.KEY_DOWN and items:
            sel = min(len(items) - 1, sel + 1)
        elif key == curses.KEY_UP:
            sel = max(0, sel - 1)
        elif key in (ord('a'), ord('A')):
            if field.options:
                available = [option for option in field.options if option not in items]
                if not available:
                    continue
                add_field = FieldSpec(
                    key=field.key,
                    label=backend.translate("ui_list_editor_add_dialog_title").format(field_label=field.label),
                    type="choice",
                    default=available[0],
                    options=available,
                    help=field.help,
                )
                new = choice_popup(stdscr, add_field, available[0], backend)
                if new and new not in items:
                    insert_at = sel + 1 if items else 0
                    items.insert(insert_at, new)
                    sel = insert_at
            else:
                new = _prompt_line(stdscr, height - 2, backend.translate("ui_list_editor_add_item_prompt"))
                if new:
                    insert_at = sel + 1 if items else 0
                    items.insert(insert_at, new)
                    sel = insert_at
        elif key in (ord('d'), ord('D')) and items:
            items.pop(sel)
            sel = max(0, min(sel, len(items) - 1))
        elif key == curses.KEY_F10:
            return items          # [] is a valid explicit empty list
        elif key == 27:
            # Peek for Alt+S (ESC + 's'/'S') = confirm
            stdscr.nodelay(True)
            next_key = stdscr.getch()
            stdscr.nodelay(False)
            if next_key in (ord('s'), ord('S')):
                return items      # Alt+S → confirm
            return current        # plain Esc → cancel
        elif key == curses.KEY_RESIZE:
            stdscr.clear()


# ---------------------------------------------------------------------------
# Generic popup (info / error)
# ---------------------------------------------------------------------------

def show_popup(stdscr: curses.window, title: str, lines: list[str], error: bool = False, press_key_label: str = "[ Press any key ]") -> None:
    height, width = stdscr.getmaxyx()
    max_line = max((len(l) for l in lines), default=0)
    box_w = min(max(max_line + 6, len(title) + 6, 28), width - 2)
    box_h = min(len(lines) + 5, height - 2)
    win = curses.newwin(box_h, box_w,
                        max(0, (height - box_h) // 2),
                        max(0, (width - box_w) // 2))
    win.keypad(True)
    win.box()

    title_attr = (curses.color_pair(_P_ERROR) if error else curses.color_pair(_P_TITLE)) | curses.A_BOLD
    t = f" {title} "
    safe_write(win, 0, max(1, (box_w - len(t)) // 2), t, title_attr)

    for i, line in enumerate(lines):
        if i + 2 >= box_h - 2:
            break
        safe_write(win, i + 2, 3, line[:box_w - 4])

    safe_write(win, box_h - 2, max(1, (box_w - len(press_key_label)) // 2), press_key_label)
    win.refresh()
    win.getch()
    del win
    stdscr.touchwin()
    stdscr.refresh()


# ---------------------------------------------------------------------------
# Confirm dialog
# ---------------------------------------------------------------------------

def confirm_loop(stdscr: curses.window, message: str, yes_label: str, no_label: str) -> bool:
    height, width = stdscr.getmaxyx()
    lines_list = message.split('\n')
    box_w = min(max((len(l) for l in lines_list), default=20) + 8, width - 4)
    box_h = len(lines_list) + 6
    win = curses.newwin(box_h, max(box_w, 24),
                        max(0, (height - box_h) // 2),
                        max(0, (width - box_w) // 2))
    win.keypad(True)
    sel = 0  # 0=No (safe default), 1=Yes

    no_btn = f"  {no_label}  "
    yes_btn = f"  {yes_label}  "
    labels = [no_btn, yes_btn]
    gap = 4
    total = len(no_btn) + gap + len(yes_btn)
    btn_start = max(1, (box_w - total) // 2)
    positions = [btn_start, btn_start + len(no_btn) + gap]

    while True:
        win.clear()
        win.box()
        for i, line in enumerate(lines_list):
            safe_write(win, i + 1, 3, line[:box_w - 4])

        for i, (label, pos) in enumerate(zip(labels, positions)):
            attr = curses.color_pair(_P_ACTIVE) if i == sel else curses.color_pair(_P_NORMAL)
            win.attron(attr)
            safe_write(win, box_h - 2, pos, label)
            win.attroff(attr)

        win.refresh()
        key = win.getch()
        if key in (curses.KEY_LEFT, curses.KEY_RIGHT, ord('\t')):
            sel = 1 - sel
        elif key in (curses.KEY_ENTER, 10, 13):
            return sel == 1
        elif key in (ord('y'), ord('Y')):
            return True
        elif key in (ord('n'), ord('N'), 27):
            return False
