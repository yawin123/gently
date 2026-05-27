from __future__ import annotations

import curses
import curses.ascii
from typing import Any

from ui.abstract import UIBackend, FormSpec, FieldSpec

# ---------------------------------------------------------------------------
# Color pair IDs (module-level constants)
# ---------------------------------------------------------------------------
_P_NORMAL = 1   # default text
_P_ACTIVE = 2   # highlighted / selected
_P_TITLE  = 3   # title bar
_P_STATUS = 4   # status bar
_P_ERROR  = 5   # error popup title


# ---------------------------------------------------------------------------
# Low-level helpers
# ---------------------------------------------------------------------------

def _setup_colors() -> None:
    if curses.has_colors():
        curses.start_color()
        curses.use_default_colors()
        curses.init_pair(_P_NORMAL, -1, -1)
        curses.init_pair(_P_ACTIVE, curses.COLOR_BLACK, curses.COLOR_CYAN)
        curses.init_pair(_P_TITLE,  curses.COLOR_CYAN,  -1)
        curses.init_pair(_P_STATUS, curses.COLOR_BLACK, curses.COLOR_WHITE)
        curses.init_pair(_P_ERROR,  curses.COLOR_WHITE, curses.COLOR_RED)


def _safe(win: curses.window, y: int, x: int, s: str, attr: int = 0) -> None:
    """addstr that silently ignores writes beyond the terminal boundary."""
    try:
        if attr:
            win.addstr(y, x, s, attr)
        else:
            win.addstr(y, x, s)
    except curses.error:
        pass


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

def _inline_editor(
    stdscr: curses.window,
    row: int, col: int, width: int,
    initial: str,
    password: bool,
) -> str | None:
    """
    Edit a single line at (row, col) with the given display width.
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
            _safe(stdscr, row, col, visible.ljust(width))
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

def _choice_popup(stdscr: curses.window, field: FieldSpec, current: Any) -> Any:
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
        title = f" {field.label} "
        _safe(win, 0, max(1, (box_w - len(title)) // 2), title, curses.A_BOLD)

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
            _safe(win, i + 2, 1, f"{marker}{options[idx]}"[:box_w - 2])
            win.attroff(attr)

        _safe(win, box_h - 1, 1,
              " ↑↓/PgUp/PgDn navigate  Enter select  Esc cancel"[:box_w - 2],
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
    _safe(stdscr, row, 0, (prompt + " " * fw)[:width - 1])
    stdscr.refresh()
    return _inline_editor(stdscr, row, len(prompt), fw, "", False)


def _list_editor(stdscr: curses.window, field: FieldSpec, current: list | None) -> list | None:
    items = list(current) if current else []
    sel = 0

    while True:
        height, width = stdscr.getmaxyx()
        stdscr.clear()

        stdscr.attron(curses.color_pair(_P_TITLE) | curses.A_BOLD)
        _safe(stdscr, 0, 0, f" Edit list: {field.label}".ljust(width))
        stdscr.attroff(curses.color_pair(_P_TITLE) | curses.A_BOLD)

        for i, item in enumerate(items):
            if i + 2 >= height - 1:
                break
            attr = curses.color_pair(_P_ACTIVE) if i == sel else curses.color_pair(_P_NORMAL)
            marker = "▶ " if i == sel else "  "
            stdscr.attron(attr)
            _safe(stdscr, i + 2, 0, f"{marker}{item}"[:width - 1])
            stdscr.attroff(attr)

        hint = " ↑↓ navigate  A add  D delete  F10/Alt+S confirm  Esc cancel"
        stdscr.attron(curses.color_pair(_P_STATUS))
        _safe(stdscr, height - 1, 0, hint[:width - 1].ljust(width - 1))
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
                    label=f"Add {field.label}",
                    type="choice",
                    default=available[0],
                    options=available,
                    help=field.help,
                )
                new = _choice_popup(stdscr, add_field, available[0])
                if new and new not in items:
                    insert_at = sel + 1 if items else 0
                    items.insert(insert_at, new)
                    sel = insert_at
            else:
                new = _prompt_line(stdscr, height - 2, "Add item: ")
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

def _popup(stdscr: curses.window, title: str, lines: list[str], error: bool = False) -> None:
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
    _safe(win, 0, max(1, (box_w - len(t)) // 2), t, title_attr)

    for i, line in enumerate(lines):
        if i + 2 >= box_h - 2:
            break
        _safe(win, i + 2, 3, line[:box_w - 4])

    _safe(win, box_h - 2, max(1, (box_w - 17) // 2), "[ Press any key ]")
    win.refresh()
    win.getch()
    del win
    stdscr.touchwin()
    stdscr.refresh()


# ---------------------------------------------------------------------------
# Form layout helpers
# ---------------------------------------------------------------------------

def _layout(form: FormSpec, width: int) -> tuple[int, int]:
    """Return (label_w, value_w) for the given terminal width."""
    label_w = max(len(f.label) for f in form.fields) + 4  # "▶ " + label + " "
    value_w = max(width - label_w - 2, 10)                # "[" + value + "]"
    return label_w, value_w


def _header_rows(form: FormSpec) -> int:
    return 1 + (1 if form.subtitle else 0) + 1  # title [+ subtitle] + blank


def _value_pos(form: FormSpec, field_idx: int, scroll: int, width: int) -> tuple[int, int, int]:
    """Return (row, col, field_width) for the value input area of a field."""
    label_w, value_w = _layout(form, width)
    row = _header_rows(form) + (field_idx - scroll)
    col = label_w + 1          # skip "["
    fw  = max(value_w - 2, 5)  # inside the brackets
    return row, col, fw


def _has_action(form: FormSpec, action_key: str) -> bool:
    if not form.actions:
        return False
    return any(key == action_key for _label, key in form.actions)


# ---------------------------------------------------------------------------
# Form draw
# ---------------------------------------------------------------------------

def _draw_form(
    stdscr: curses.window,
    form: FormSpec,
    values: dict[str, Any],
    current: int,
    scroll: int,
) -> None:
    height, width = stdscr.getmaxyx()
    stdscr.clear()
    label_w, value_w = _layout(form, width)
    hr = _header_rows(form)

    # Title
    stdscr.attron(curses.color_pair(_P_TITLE) | curses.A_BOLD)
    _safe(stdscr, 0, 0, f" {form.title}".ljust(width))
    stdscr.attroff(curses.color_pair(_P_TITLE) | curses.A_BOLD)

    row = 1
    if form.subtitle:
        _safe(stdscr, row, 1, form.subtitle[:width - 2])
        row += 1
    row += 1  # blank separator

    visible_count = height - hr - 1
    for rel, field in enumerate(form.fields[scroll:scroll + visible_count]):
        abs_idx = scroll + rel
        active = abs_idx == current
        marker = "▶ " if active else "  "
        val_str = _fmt(field, values.get(field.key))
        label_part = f"{marker}{field.label}"
        val_display = val_str[:value_w - 2]

        attr = curses.color_pair(_P_ACTIVE) if active else curses.color_pair(_P_NORMAL)
        stdscr.attron(attr)
        _safe(stdscr, row, 0,
              f"{label_part:<{label_w}}[{val_display:<{value_w - 2}}]"[:width - 1])
        stdscr.attroff(attr)
        row += 1

    # Status bar
    stdscr.attron(curses.color_pair(_P_STATUS))
    hint = " ↑↓/Tab navigate  Enter edit  Space toggle  F10/Alt+S save  Esc cancel  ? help"
    if _has_action(form, "delete"):
        hint = " ↑↓/Tab navigate  Enter edit  Space toggle  F10 save  D delete  Esc cancel  ? help"
    _safe(stdscr, height - 1, 0, hint[:width - 1].ljust(width - 1))
    stdscr.attroff(curses.color_pair(_P_STATUS))

    stdscr.refresh()


# ---------------------------------------------------------------------------
# Form main loop
# ---------------------------------------------------------------------------

def _form_loop(
    stdscr: curses.window,
    form: FormSpec,
    values: dict[str, Any],
    result: list,
) -> None:
    current = 0
    scroll = 0
    n = len(form.fields)

    while True:
        height, width = stdscr.getmaxyx()
        hr = _header_rows(form)
        visible = height - hr - 1

        # Keep current field in view
        if current < scroll:
            scroll = current
        elif current >= scroll + visible:
            scroll = current - visible + 1
        scroll = max(0, scroll)

        _draw_form(stdscr, form, values, current, scroll)
        key = stdscr.getch()

        if key in (curses.KEY_DOWN, ord('\t')):
            current = (current + 1) % n
        elif key in (curses.KEY_UP, curses.KEY_BTAB):
            current = (current - 1) % n
        elif key == curses.KEY_F10:          # F10
            result[0] = values
            return
        elif key == 27:                      # Esc or Alt+sequence
            # Peek for Alt+S (ESC + 's'/'S') = confirm
            stdscr.nodelay(True)
            next_key = stdscr.getch()
            stdscr.nodelay(False)
            if next_key in (ord('s'), ord('S')):
                result[0] = values   # Alt+S → confirm
            else:
                result[0] = None      # plain Esc → cancel
            return
        elif key in (ord('d'), ord('D')) and _has_action(form, "delete"):
            result[0] = {"__action__": "delete"}
            return
        elif key in (curses.KEY_ENTER, 10, 13):
            field = form.fields[current]
            height, width = stdscr.getmaxyx()
            row, col, fw = _value_pos(form, current, scroll, width)

            if field.type in ("text", "int", "password"):
                initial = str(values[field.key]) if values[field.key] is not None else ""
                new = _inline_editor(stdscr, row, col, fw, initial,
                                     field.type == "password")
                if new is not None:
                    if field.type == "int":
                        try:
                            values[field.key] = int(new) if new else None
                        except ValueError:
                            pass
                    else:
                        values[field.key] = new or None
            elif field.type == "choice":
                values[field.key] = _choice_popup(stdscr, field, values[field.key])
            elif field.type == "bool":
                values[field.key] = not bool(values[field.key])
            elif field.type == "list":
                values[field.key] = _list_editor(stdscr, field, values[field.key])
            elif field.type == "subsection":
                result[0] = {"__action__": "subsection", "__field__": field.key, "__values__": dict(values)}
                return
        elif key == ord(' '):
            if form.fields[current].type == "bool":
                values[form.fields[current].key] = not bool(
                    values[form.fields[current].key]
                )
        elif key == ord('?'):
            field = form.fields[current]
            if field.help:
                _popup(stdscr, "Help", field.help.split('\n'))
        elif key == curses.KEY_RESIZE:
            stdscr.clear()


# ---------------------------------------------------------------------------
# Summary loop
# ---------------------------------------------------------------------------

def _summary_loop(
    stdscr: curses.window,
    sections: list[tuple[str, dict]],
    result: list,
) -> None:
    section_keys = [k for k, _ in sections]

    # Build flat list of display lines: (type, section_key, text)
    lines: list[tuple[str, str, str]] = []
    for section_key, data in sections:
        lines.append(("header", section_key, section_key))
        for k, v in data.items():
            lines.append(("field", section_key, f"  {k}: {v}"))
        lines.append(("blank", "", ""))

    actions = [("Install", "install"), ("Edit section", "edit"), ("Save & exit", "save_and_exit")]
    sel = 0
    scroll = 0

    while True:
        height, width = stdscr.getmaxyx()
        content_h = height - 3
        stdscr.clear()

        stdscr.attron(curses.color_pair(_P_TITLE) | curses.A_BOLD)
        _safe(stdscr, 0, 0, " Configuration Summary".ljust(width))
        stdscr.attroff(curses.color_pair(_P_TITLE) | curses.A_BOLD)

        for i, (ltype, _lkey, ltext) in enumerate(lines[scroll:scroll + content_h]):
            row = i + 1
            if ltype == "header":
                _safe(stdscr, row, 0, f"[{ltext}]"[:width - 1], curses.A_BOLD)
            elif ltype == "field":
                _safe(stdscr, row, 0, ltext[:width - 1])

        # Action bar
        col = 2
        for i, (label, _key) in enumerate(actions):
            attr = curses.color_pair(_P_ACTIVE) if i == sel else curses.color_pair(_P_NORMAL)
            stdscr.attron(attr)
            _safe(stdscr, height - 2, col, f" {label} ")
            stdscr.attroff(attr)
            col += len(label) + 4

        stdscr.attron(curses.color_pair(_P_STATUS))
        _safe(stdscr, height - 1, 0,
              " ↑↓ scroll  ←→/Tab action  Enter confirm"[:width - 1].ljust(width - 1))
        stdscr.attroff(curses.color_pair(_P_STATUS))
        stdscr.refresh()

        key = stdscr.getch()
        if key == curses.KEY_DOWN:
            scroll = min(scroll + 1, max(0, len(lines) - content_h))
        elif key == curses.KEY_UP:
            scroll = max(0, scroll - 1)
        elif key in (curses.KEY_RIGHT, ord('\t')):
            sel = (sel + 1) % len(actions)
        elif key in (curses.KEY_LEFT, curses.KEY_BTAB):
            sel = (sel - 1) % len(actions)
        elif key in (curses.KEY_ENTER, 10, 13):
            action_key = actions[sel][1]
            if action_key == "edit" and section_keys:
                chosen = _choice_popup(
                    stdscr,
                    FieldSpec(key="_sec", label="Select section to edit",
                              type="choice", options=section_keys),
                    section_keys[0],
                )
                result[0] = f"edit:{chosen}"
            else:
                result[0] = action_key
            return
        elif key == 27:
            result[0] = "save_and_exit"
            return
        elif key == curses.KEY_RESIZE:
            stdscr.clear()


# ---------------------------------------------------------------------------
# Partition section loop
# ---------------------------------------------------------------------------

def _subsection_loop(
    stdscr: curses.window,
    title: str,
    items: list[tuple[str, dict]],
    result: list,
) -> None:
    selected = 0
    scroll = 0

    while True:
        height, width = stdscr.getmaxyx()
        content_h = height - 2
        stdscr.clear()

        stdscr.attron(curses.color_pair(_P_TITLE) | curses.A_BOLD)
        _safe(stdscr, 0, 0, f" {title}".ljust(width))
        stdscr.attroff(curses.color_pair(_P_TITLE) | curses.A_BOLD)

        rows: list[tuple[str, str]] = []
        if not items:
            rows.append(("empty", "(no items yet)"))
        for idx, (name, data) in enumerate(items):
            summary = ", ".join(f"{k}={v}" for k, v in data.items()) if data else "(empty)"
            rows.append(("item", f"{idx + 1}. {name}  {summary}"))
        rows.append(("add", "+ Add new"))
        rows.append(("done", "Done"))

        selected = max(0, min(selected, len(rows) - 1))
        if selected < scroll:
            scroll = selected
        elif selected >= scroll + content_h:
            scroll = selected - content_h + 1

        for i, (kind, text) in enumerate(rows[scroll:scroll + content_h]):
            row_index = scroll + i
            row = i + 1
            active = row_index == selected
            attr = curses.color_pair(_P_ACTIVE) if active else curses.color_pair(_P_NORMAL)
            marker = "▶ " if active else "  "
            stdscr.attron(attr)
            _safe(stdscr, row, 0, f"{marker}{text}"[:width - 1])
            stdscr.attroff(attr)

        stdscr.attron(curses.color_pair(_P_STATUS))
        _safe(stdscr, height - 1, 0,
              " ↑↓ navigate  Enter select  Esc done"[:width - 1].ljust(width - 1))
        stdscr.attroff(curses.color_pair(_P_STATUS))
        stdscr.refresh()

        key = stdscr.getch()
        if key == curses.KEY_DOWN:
            selected = min(len(rows) - 1, selected + 1)
        elif key == curses.KEY_UP:
            selected = max(0, selected - 1)
        elif key in (curses.KEY_ENTER, 10, 13):
            kind, _text = rows[selected]
            if kind == "item":
                item_index = sum(1 for k, _ in rows[:selected] if k == "item")
                result[0] = f"edit:{item_index}"
            elif kind == "add":
                result[0] = "add"
            else:
                result[0] = "done"
            return
        elif key == 27:
            result[0] = "done"
            return
        elif key == curses.KEY_RESIZE:
            stdscr.clear()


# ---------------------------------------------------------------------------
# Confirm loop
# ---------------------------------------------------------------------------

def _confirm_loop(stdscr: curses.window, message: str) -> bool:
    height, width = stdscr.getmaxyx()
    lines = message.split('\n')
    box_w = min(max((len(l) for l in lines), default=20) + 8, width - 4)
    box_h = len(lines) + 6
    win = curses.newwin(box_h, max(box_w, 24),
                        max(0, (height - box_h) // 2),
                        max(0, (width - box_w) // 2))
    win.keypad(True)
    sel = 0  # 0=No (safe default), 1=Yes

    while True:
        win.clear()
        win.box()
        for i, line in enumerate(lines):
            _safe(win, i + 1, 3, line[:box_w - 4])

        labels = ["  No  ", "  Yes  "]
        positions = [box_w // 2 - 9, box_w // 2 + 1]
        for i, (label, pos) in enumerate(zip(labels, positions)):
            attr = curses.color_pair(_P_ACTIVE) if i == sel else curses.color_pair(_P_NORMAL)
            win.attron(attr)
            _safe(win, box_h - 2, pos, label)
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


# ---------------------------------------------------------------------------
# CursesBackend
# ---------------------------------------------------------------------------

class CursesBackend(UIBackend):

    def show_form(self, form: FormSpec) -> dict[str, Any] | None:
        values = {f.key: f.default for f in form.fields}
        result: list[dict[str, Any] | None] = [values]

        def _run(stdscr: curses.window) -> None:
            _setup_colors()
            curses.curs_set(0)
            stdscr.keypad(True)
            _form_loop(stdscr, form, values, result)

        curses.wrapper(_run)
        return result[0]

    def show_subsection(self, title: str, items: list[tuple[str, dict]]) -> str:
        result = ["done"]

        def _run(stdscr: curses.window) -> None:
            _setup_colors()
            curses.curs_set(0)
            stdscr.keypad(True)
            _subsection_loop(stdscr, title, items, result)

        curses.wrapper(_run)
        return result[0]

    def show_summary(self, sections: list[tuple[str, dict]]) -> str:
        result = ["save_and_exit"]

        def _run(stdscr: curses.window) -> None:
            _setup_colors()
            curses.curs_set(0)
            stdscr.keypad(True)
            _summary_loop(stdscr, sections, result)

        curses.wrapper(_run)
        return result[0]

    def show_progress(self, phase: str, message: str) -> None:
        # Milestone 5 will replace this with a persistent curses progress view.
        print(f"[{phase}] {message}", flush=True)

    def show_error(self, message: str) -> None:
        def _run(stdscr: curses.window) -> None:
            _setup_colors()
            stdscr.keypad(True)
            _popup(stdscr, "Error", message.split('\n'), error=True)

        curses.wrapper(_run)

    def show_confirm(self, message: str) -> bool:
        result = [False]

        def _run(stdscr: curses.window) -> None:
            _setup_colors()
            curses.curs_set(0)
            stdscr.keypad(True)
            result[0] = _confirm_loop(stdscr, message)

        curses.wrapper(_run)
        return result[0]

    def show_info(self, title: str, lines: list[str]) -> None:
        def _run(stdscr: curses.window) -> None:
            _setup_colors()
            stdscr.keypad(True)
            _popup(stdscr, title, lines)

        curses.wrapper(_run)
