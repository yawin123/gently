from __future__ import annotations

import curses
import curses.ascii
import os
import queue
import sys
import threading
import time
from typing import Any

from ui.abstract import UIBackend, FormSpec, FieldSpec

# Reduce the ESC-key disambiguation timeout from the ncurses default (1000 ms)
# to 25 ms. Without this, every Esc keypress has a noticeable lag because the
# terminal waits to see if ESC is the start of a multi-byte escape sequence.
os.environ.setdefault("ESCDELAY", "25")

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

def _choice_popup(stdscr: curses.window, field: FieldSpec, current: Any, backend: UIBackend) -> Any:
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
    _safe(stdscr, row, 0, (prompt + " " * fw)[:width - 1])
    stdscr.refresh()
    return _inline_editor(stdscr, row, len(prompt), fw, "", False)


def _list_editor(stdscr: curses.window, field: FieldSpec, current: list | None, backend: UIBackend) -> list | None:
    items = list(current) if current else []
    sel = 0

    while True:
        height, width = stdscr.getmaxyx()
        stdscr.clear()

        stdscr.attron(curses.color_pair(_P_TITLE) | curses.A_BOLD)
        _safe(stdscr, 0, 0, (" " + backend.translate("ui_list_editor_title").format(field_label=field.label)).ljust(width))
        stdscr.attroff(curses.color_pair(_P_TITLE) | curses.A_BOLD)

        for i, item in enumerate(items):
            if i + 2 >= height - 1:
                break
            attr = curses.color_pair(_P_ACTIVE) if i == sel else curses.color_pair(_P_NORMAL)
            marker = "▶ " if i == sel else "  "
            stdscr.attron(attr)
            _safe(stdscr, i + 2, 0, f"{marker}{item}"[:width - 1])
            stdscr.attroff(attr)

        hint = backend.translate("ui_list_editor_hint")
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
                    label=backend.translate("ui_list_editor_add_dialog_title").format(field_label=field.label),
                    type="choice",
                    default=available[0],
                    options=available,
                    help=field.help,
                )
                new = _choice_popup(stdscr, add_field, available[0], backend)
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

def _popup(stdscr: curses.window, title: str, lines: list[str], error: bool = False, press_key_label: str = "[ Press any key ]") -> None:
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

    _safe(win, box_h - 2, max(1, (box_w - len(press_key_label)) // 2), press_key_label)
    win.refresh()
    win.getch()
    del win
    stdscr.touchwin()
    stdscr.refresh()


# ---------------------------------------------------------------------------
# Form layout helpers
# ---------------------------------------------------------------------------

def _layout(form: FormSpec, width: int, values: dict[str, Any] | None = None) -> tuple[int, int]:
    """Return (label_w, value_w) for the given terminal width."""
    fields = _visible_fields(form, values)
    label_w = max(len(f.label) for f in fields) + 4 if fields else 20
    value_w = max(width - label_w - 2, 10)
    return label_w, value_w


def _visible_fields(form: FormSpec, values: dict[str, Any] | None) -> list[FieldSpec]:
    if values is None:
        return [f for f in form.fields if f.visible_when is None]
    result = []
    for f in form.fields:
        if f.visible_when is not None:
            cond_key, cond_val = f.visible_when
            if values.get(cond_key) != cond_val:
                continue
        result.append(f)
    return result


def _header_rows(form: FormSpec) -> int:
    return 1 + (1 if form.subtitle else 0) + 1  # title [+ subtitle] + blank


def _value_pos(form: FormSpec, field_idx: int, scroll: int, width: int, values: dict[str, Any] | None = None) -> tuple[int, int, int]:
    """Return (row, col, field_width) for the value input area of a field."""
    label_w, value_w = _layout(form, width, values)
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
    backend: UIBackend,
) -> None:
    height, width = stdscr.getmaxyx()
    stdscr.clear()
    visible = _visible_fields(form, values)
    label_w, value_w = _layout(form, width, values)
    hr = _header_rows(form)

    # Title — translate at render time so F2 language switch works.
    title_text = backend.translate(form.title) if form.title else ""
    stdscr.attron(curses.color_pair(_P_TITLE) | curses.A_BOLD)
    _safe(stdscr, 0, 0, f" {title_text}".ljust(width))
    stdscr.attroff(curses.color_pair(_P_TITLE) | curses.A_BOLD)

    row = 1
    if form.subtitle:
        subtitle_text = backend.translate(form.subtitle)
        _safe(stdscr, row, 1, subtitle_text[:width - 2])
        row += 1
    row += 1  # blank separator

    visible_count = height - hr - 1
    for rel, field in enumerate(visible[scroll:scroll + visible_count]):
        abs_idx = scroll + rel
        active = abs_idx == current
        marker = "▶ " if active else "  "
        val_str = _fmt(field, values.get(field.key))
        # Translate label at render time.
        label_text = backend.translate(field.i18n_key or field.key) if field.i18n_key else field.label
        label_part = f"{marker}{label_text}"
        val_display = val_str[:value_w - 2]

        attr = curses.color_pair(_P_ACTIVE) if active else curses.color_pair(_P_NORMAL)
        stdscr.attron(attr)
        _safe(stdscr, row, 0,
              f"{label_part:<{label_w}}[{val_display:<{value_w - 2}}]"[:width - 1])
        stdscr.attroff(attr)
        row += 1

    # Status bar
    stdscr.attron(curses.color_pair(_P_STATUS))
    hint = backend.translate("ui_form_hint_default")
    if _has_action(form, "delete"):
        hint = backend.translate("ui_form_hint_with_delete")
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
    backend: UIBackend,
) -> None:
    current = 0
    scroll = 0

    while True:
        height, width = stdscr.getmaxyx()
        hr = _header_rows(form)
        visible = _visible_fields(form, values)
        n = len(visible)
        max_visible = height - hr - 1

        # Keep current field in view
        if current < scroll:
            scroll = current
        elif current >= scroll + max_visible:
            scroll = current - max_visible + 1
        scroll = max(0, scroll)

        _draw_form(stdscr, form, values, current, scroll, backend)
        try:
            key = stdscr.getch()
        except KeyboardInterrupt:
            backend.interrupt()
            return

        if key in (curses.KEY_DOWN, ord('\t')):
            current = (current + 1) % n
        elif key in (curses.KEY_UP, curses.KEY_BTAB):
            current = (current - 1) % n
        elif key == curses.KEY_F2:
            _cycle_language(stdscr, backend)
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
            field = visible[current]
            height, width = stdscr.getmaxyx()
            row, col, fw = _value_pos(form, current, scroll, width, values)

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
                values[field.key] = _choice_popup(stdscr, field, values[field.key], backend)
            elif field.type == "bool":
                values[field.key] = not bool(values[field.key])
            elif field.type == "list":
                values[field.key] = _list_editor(stdscr, field, values[field.key], backend)
            elif field.type == "subsection":
                result[0] = {"__action__": "subsection", "__field__": field.key, "__values__": dict(values)}
                return
        elif key == ord(' '):
            if visible[current].type == "bool":
                values[visible[current].key] = not bool(
                    values[visible[current].key]
                )
        elif key == ord('?'):
            field = visible[current]
            if field.help:
                help_text = backend.translate(field.help)
                _popup(stdscr, backend.translate("ui_help_popup_title"), help_text.split('\n'),
                       press_key_label=backend.translate("ui_press_any_key"))
        elif key == curses.KEY_RESIZE:
            stdscr.clear()


# ---------------------------------------------------------------------------
# Summary loop
# ---------------------------------------------------------------------------

def _section_menu_loop(
    stdscr: curses.window,
    sections: list[tuple[str, str, bool]],  # (key, name, is_complete)
    all_complete: bool,
    result: list,
    backend: UIBackend,
) -> None:
    selected = 0

    while True:
        height, width = stdscr.getmaxyx()
        stdscr.erase()

        # Title bar
        title = backend.translate("ui_menu_title")
        stdscr.attron(curses.color_pair(_P_TITLE) | curses.A_BOLD)
        _safe(stdscr, 0, 0, title.ljust(width))
        stdscr.attroff(curses.color_pair(_P_TITLE) | curses.A_BOLD)

        # Section list
        for i, (_key, name, complete) in enumerate(sections):
            row = 2 + i
            if row >= height - 2:
                break
            icon = "\u2713" if complete else " "
            line = f"  {icon}  {name}"
            if i == selected:
                _safe(stdscr, row, 0, line.ljust(width - 1), curses.color_pair(_P_ACTIVE))
            else:
                _safe(stdscr, row, 0, line[:width - 1])

        # Status bar
        hint = backend.translate("ui_menu_hint")
        if all_complete:
            hint += backend.translate("ui_menu_hint_install_suffix")
        stdscr.attron(curses.color_pair(_P_STATUS))
        _safe(stdscr, height - 1, 0, hint[:width - 1].ljust(width - 1))
        stdscr.attroff(curses.color_pair(_P_STATUS))

        stdscr.refresh()

        try:
            key = stdscr.getch()
        except KeyboardInterrupt:
            backend.interrupt()
            return

        if key == curses.KEY_UP:
            selected = max(0, selected - 1)
        elif key == curses.KEY_DOWN:
            selected = min(len(sections) - 1, selected + 1)
        elif key in (curses.KEY_ENTER, 10, 13):
            result[0] = f"edit:{sections[selected][0]}"
            return
        elif key in (ord('s'), ord('S')):
            result[0] = "save_and_exit"
            return
        elif key in (ord('i'), ord('I')) and all_complete:
            result[0] = "install"
            return
        elif key == curses.KEY_F2:
            _cycle_language(stdscr, backend)
        elif key == 27:  # Esc
            backend.interrupt()
            return
        elif key == curses.KEY_RESIZE:
            stdscr.clear()


def _summary_loop(
    stdscr: curses.window,
    sections: list[tuple[str, dict]],
    result: list,
    backend: UIBackend,
) -> None:
    section_keys = [k for k, _ in sections]

    def _render_lines(value: Any, indent: int) -> list[str]:
        pad = "  " * indent
        if isinstance(value, dict):
            lines: list[str] = []
            for k, v in value.items():
                if isinstance(v, (dict, list)):
                    lines.append(f"{pad}{k}:")
                    lines.extend(_render_lines(v, indent + 1))
                else:
                    lines.append(f"{pad}{k}: {v}")
            return lines
        if isinstance(value, list):
            lines = []
            for i, item in enumerate(value, start=1):
                if isinstance(item, (dict, list)):
                    lines.append(f"{pad}- item {i}:")
                    lines.extend(_render_lines(item, indent + 1))
                else:
                    lines.append(f"{pad}- {item}")
            return lines
        return [f"{pad}{value}"]

    # Build flat list of display lines: (type, section_key, text)
    lines: list[tuple[str, str, str]] = []
    for section_key, data in sections:
        lines.append(("header", section_key, section_key))
        for rendered in _render_lines(data, 1):
            lines.append(("field", section_key, rendered))
        lines.append(("blank", "", ""))

    sel = 0
    scroll = 0

    while True:
        # Rebuild actions inside the loop so they are re-translated on F2.
        actions = [
            (backend.translate("ui_summary_action_save"), "save_and_exit"),
            (backend.translate("ui_summary_action_cancel"), "cancel"),
        ]
        height, width = stdscr.getmaxyx()
        content_h = height - 3
        stdscr.clear()

        stdscr.attron(curses.color_pair(_P_TITLE) | curses.A_BOLD)
        _safe(stdscr, 0, 0, backend.translate("ui_summary_title").ljust(width))
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
        hint = backend.translate("ui_summary_hint")
        _safe(stdscr, height - 1, 0,
              hint[:width - 1].ljust(width - 1))
        stdscr.attroff(curses.color_pair(_P_STATUS))
        stdscr.refresh()

        try:
            key = stdscr.getch()
        except KeyboardInterrupt:
            backend.interrupt()
            return
        if key == curses.KEY_DOWN:
            scroll = min(scroll + 1, max(0, len(lines) - content_h))
        elif key == curses.KEY_UP:
            scroll = max(0, scroll - 1)
        elif key == curses.KEY_F2:
            _cycle_language(stdscr, backend)
        elif key in (curses.KEY_RIGHT, ord('\t')):
            sel = (sel + 1) % len(actions)
        elif key in (curses.KEY_LEFT, curses.KEY_BTAB):
            sel = (sel - 1) % len(actions)
        elif key in (curses.KEY_ENTER, 10, 13):
            action_key = actions[sel][1]
            if action_key == "cancel" and section_keys:
                chosen = _choice_popup(
                    stdscr,
                    FieldSpec(key="_sec", label=backend.translate("ui_summary_section_selector_label"),
                              type="choice", options=section_keys),
                    section_keys[0],
                    backend,
                )
                result[0] = f"edit:{chosen}"
            else:
                result[0] = action_key
            return
        elif key == 27:
            if section_keys:
                chosen = _choice_popup(
                    stdscr,
                    FieldSpec(key="_sec", label=backend.translate("ui_summary_section_selector_label"),
                              type="choice", options=section_keys),
                    section_keys[0],
                    backend,
                )
                result[0] = f"edit:{chosen}"
            else:
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
    backend: UIBackend,
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
            rows.append(("empty", backend.translate("ui_subsection_no_items")))
        for idx, (name, data) in enumerate(items):
            summary = ", ".join(f"{k}={v}" for k, v in data.items()) if data else "(empty)"
            rows.append(("item", f"{idx + 1}. {name}  {summary}"))
        rows.append(("add", backend.translate("ui_subsection_add_new")))
        rows.append(("done", backend.translate("ui_subsection_done")))

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
        hint = backend.translate("ui_subsection_hint")
        _safe(stdscr, height - 1, 0,
              hint[:width - 1].ljust(width - 1))
        stdscr.attroff(curses.color_pair(_P_STATUS))
        stdscr.refresh()

        try:
            key = stdscr.getch()
        except KeyboardInterrupt:
            backend.interrupt()
            return
        if key == curses.KEY_DOWN:
            selected = min(len(rows) - 1, selected + 1)
        elif key == curses.KEY_UP:
            selected = max(0, selected - 1)
        elif key == curses.KEY_F2:
            _cycle_language(stdscr, backend)
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
# Language switching
# ---------------------------------------------------------------------------

def _cycle_language(stdscr: curses.window, backend: UIBackend) -> None:
    langs = backend.available_languages()
    if len(langs) < 2:
        return
    current = backend.current_language()
    chosen = _choice_popup(
        stdscr,
        FieldSpec(key="lang", label=backend.translate("ui_language_label"), type="choice", options=langs),
        current,
        backend,
    )
    if chosen is not None:
        backend.reload_language(chosen)


# ---------------------------------------------------------------------------
# Confirm loop
# ---------------------------------------------------------------------------

def _confirm_loop(stdscr: curses.window, message: str, yes_label: str, no_label: str) -> bool:
    height, width = stdscr.getmaxyx()
    lines = message.split('\n')
    box_w = min(max((len(l) for l in lines), default=20) + 8, width - 4)
    box_h = len(lines) + 6
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
        for i, line in enumerate(lines):
            _safe(win, i + 1, 3, line[:box_w - 4])

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
            _form_loop(stdscr, form, values, result, self)

        curses.wrapper(_run)
        return result[0]

    def show_subsection(self, title: str, items: list[tuple[str, dict]]) -> str:
        result = ["done"]

        def _run(stdscr: curses.window) -> None:
            _setup_colors()
            curses.curs_set(0)
            stdscr.keypad(True)
            _subsection_loop(stdscr, title, items, result, self)

        curses.wrapper(_run)
        return result[0]

    def show_section_menu(
        self,
        sections: list[tuple[str, str, bool]],
        all_complete: bool,
    ) -> str:
        result = ["save_and_exit"]

        def _run(stdscr: curses.window) -> None:
            _setup_colors()
            curses.curs_set(0)
            stdscr.keypad(True)
            _section_menu_loop(stdscr, sections, all_complete, result, self)

        curses.wrapper(_run)
        return result[0]

    def show_summary(self, sections: list[tuple[str, dict]]) -> str:
        result = ["save_and_exit"]

        def _run(stdscr: curses.window) -> None:
            _setup_colors()
            curses.curs_set(0)
            stdscr.keypad(True)
            _summary_loop(stdscr, sections, result, self)

        curses.wrapper(_run)
        return result[0]

    def show_progress(self, phase: str, message: str) -> None:
        print(f"[{phase}] {message}", flush=True)

    # ── Full-screen installation log ───────────────────────

    def prepare_install(self, phases: list) -> None:
        """Set up internal state for the installation UI.

        Must be called (from the main thread) before starting the installation
        thread so that the queue is ready when the first progress events arrive.
        """
        self._install_phases = [
            {"key": p.key, "title": p.title, "status": "pending", "duration_sec": None,
             "log": [], "expanded": False, "log_scroll": 0}
            for p in phases
        ]
        self._install_phase_index: dict[str, int] = {p.key: i for i, p in enumerate(phases)}
        self._install_queue: queue.Queue = queue.Queue()
        self._install_finished = False
        self._install_selected = 0
        # No _install_ui_thread — the UI runs on the main thread via run_install_ui().
        self.__dict__.pop("_install_ui_thread", None)

    def run_install_ui(self) -> None:
        """Block on the calling (main) thread driving the curses installation UI.

        The installation must be running in a background thread.
        Returns once the user closes the UI (after installation completes or fails).
        """
        try:
            curses.wrapper(self._install_live_loop)
        except KeyboardInterrupt:
            raise

    def install_progress_begin(self, phase_keys: list[str]) -> None:
        """Initialise phase data. Becomes a no-op when prepare_install() was already called.

        Standalone mode: if prepare_install() was not called beforehand, the
        installation is assumed to run on the main thread; a background UI thread
        is started so curses does not conflict with the calling thread.
        """
        if hasattr(self, "_install_queue"):
            # Queue already set up by prepare_install(); nothing to do.
            return

        # Standalone mode: installation runs on the main thread, UI on a daemon thread.
        self._install_phases = [
            {"key": pk, "title": pk, "status": "pending", "duration_sec": None,
             "log": [], "expanded": False, "log_scroll": 0}
            for pk in phase_keys
        ]
        self._install_phase_index = {pk: i for i, pk in enumerate(phase_keys)}
        self._install_queue = queue.Queue()
        self._install_finished = False
        self._install_selected = 0
        self._install_ui_thread = threading.Thread(
            target=lambda: curses.wrapper(self._install_live_loop),
            daemon=True,
            name="gently-install-ui",
        )
        self._install_ui_thread.start()

    def install_progress_update(self, phase_key: str, message: str) -> None:
        """Send a log line to the live UI thread via the queue."""
        if hasattr(self, "_install_queue"):
            self._install_queue.put(("log", phase_key, message))

    def install_progress_end(self, report: Any) -> None:
        """Signal the UI thread that installation is done, then wait for the user to close it."""
        if hasattr(self, "_install_queue"):
            self._install_queue.put(("done", report))
        if hasattr(self, "_install_ui_thread"):
            self._install_ui_thread.join()

    # ── Internal: live installation log loop ──────────────

    def _install_live_loop(self, stdscr: curses.window) -> None:
        """Live installation UI driven by _install_queue. Runs in a background thread."""
        _setup_colors()
        curses.curs_set(0)
        stdscr.keypad(True)
        stdscr.nodelay(True)  # non-blocking getch while installing
        self._install_stdscr = stdscr
        phases = self._install_phases

        while True:
            # ── Drain incoming queue messages ───────────────────
            try:
                while True:
                    msg = self._install_queue.get_nowait()
                    kind = msg[0]
                    if kind == "log":
                        _, phase_key, message = msg
                        idx = self._install_phase_index.get(phase_key)
                        if idx is not None:
                            ph = phases[idx]
                            if ph["status"] == "pending":
                                ph["status"] = "running"
                            ph["log"].append(message)
                            # (scroll is handled by _install_redraw)
                    elif kind == "done":
                        _, report = msg
                        for phase_result in report.phases:
                            idx2 = self._install_phase_index.get(phase_result.key)
                            if idx2 is not None:
                                ph = phases[idx2]
                                ph["status"] = "done" if phase_result.status == "ok" else "failed"
                                ph["duration_sec"] = phase_result.duration_sec
                                if phase_result.error:
                                    ph["log"].append(f"ERROR: {phase_result.error}")
                        for ph in phases:
                            if ph["status"] in ("pending", "running"):
                                ph["status"] = "done"
                            # Pin every phase to the bottom so the final redraw
                            # shows the last lines, not a stale mid-log position.
                            ph["log_scroll"] = len(ph["log"])
                        self._install_finished = True
                        # Redraw immediately with the final state BEFORE switching
                        # to blocking getch. Without this call the display would
                        # stay frozen on the last pre-done frame until the user
                        # presses a key.
                        self._install_redraw()
                        stdscr.nodelay(False)  # switch to blocking getch for review
            except queue.Empty:
                pass

            # ── Keyboard input ───────────────────────────────────
            selected = self._install_selected
            try:
                key = stdscr.getch()
            except KeyboardInterrupt:
                return

            if self._install_finished and key in (ord('q'), ord('Q'), 27):
                return
            elif key == curses.KEY_DOWN:
                selected = min(len(phases) - 1, selected + 1)
            elif key == curses.KEY_UP:
                selected = max(0, selected - 1)
            elif key in (curses.KEY_ENTER, 10, 13, ord(' ')):
                ph = phases[selected]
                if ph["status"] != "pending":
                    ph["expanded"] = not ph["expanded"]
                    ph["log_scroll"] = max(0, len(ph["log"]) - 1) if ph["expanded"] else 0
            elif key == curses.KEY_RESIZE:
                pass
            elif key == curses.KEY_NPAGE:
                ph = phases[selected]
                if ph["expanded"] and ph["log"]:
                    ph["log_scroll"] = min(len(ph["log"]) - 1, ph["log_scroll"] + 5)
            elif key == curses.KEY_PPAGE:
                ph = phases[selected]
                if ph["expanded"] and ph["log"]:
                    ph["log_scroll"] = max(0, ph["log_scroll"] - 5)

            self._install_selected = selected

            # ── Redraw ───────────────────────────────────────────
            self._install_redraw()
            if not self._install_finished:
                time.sleep(0.05)  # ~20 fps while installing

    def _install_redraw(self) -> None:
        """Redraw the interactive installation log screen."""
        try:
            stdscr = getattr(self, "_install_stdscr", None)
            if stdscr is None:
                return
            phases = getattr(self, "_install_phases", [])
            finished = getattr(self, "_install_finished", False)
            height, width = stdscr.getmaxyx()
            stdscr.clear()

            # Title bar
            if finished:
                title = self.translate("ui_install_title_done")
            else:
                title = self.translate("ui_install_title_running")
            stdscr.attron(curses.color_pair(_P_TITLE) | curses.A_BOLD)
            _safe(stdscr, 0, 0, title.ljust(width))
            stdscr.attroff(curses.color_pair(_P_TITLE) | curses.A_BOLD)

            # Phase list
            selected = self._install_selected
            row = 1
            for i, ph in enumerate(phases):
                if row >= height - 1:
                    break
                # Status icon
                if ph["status"] == "pending":
                    icon = " "
                elif ph["status"] == "running":
                    icon = "▶"
                elif ph["status"] == "done":
                    icon = "✓"
                else:
                    icon = "✗"
                # Duration
                dur_str = ""
                if ph["duration_sec"] is not None:
                    dur_str = f" ({ph['duration_sec']:.1f}s)"
                # Active/highlight
                active = i == selected
                attr = curses.color_pair(_P_ACTIVE) if active else curses.color_pair(_P_NORMAL)
                marker = "▶ " if active else "  "
                line = f"{marker}{icon} {ph['title']}{dur_str}"
                stdscr.attron(attr)
                _safe(stdscr, row, 1, line[:width - 2])
                stdscr.attroff(attr)
                row += 1

                # Expanded log
                if ph["expanded"] and ph["log"]:
                    log_lines = ph["log"]
                    log_h = max(1, min(len(log_lines), height - row - 2))
                    if ph["status"] == "running":
                        # Follow mode: always pin to the last lines while executing.
                        log_scroll = max(0, len(log_lines) - log_h)
                    else:
                        log_scroll = max(0, min(ph["log_scroll"], max(0, len(log_lines) - log_h)))
                    ph["log_scroll"] = log_scroll
                    for j in range(log_h):
                        if row >= height - 1:
                            break
                        log_idx = log_scroll + j
                        if log_idx < len(log_lines):
                            log_line = log_lines[log_idx]
                            is_last_log = (log_idx == len(log_lines) - 1) and ph["status"] == "running"
                            line_attr = curses.A_BOLD if is_last_log else curses.A_NORMAL
                            _safe(stdscr, row, 4, f"  {log_line}"[:width - 6], line_attr)
                        row += 1

            # Status bar
            hint = self.translate("ui_install_hint")
            _safe(stdscr, height - 1, 0, hint.ljust(width - 1), curses.color_pair(_P_STATUS))
            stdscr.refresh()
        except Exception:
            pass

    def show_error(self, title_key: str, message: str, ok_key: str) -> None:
        def _run(stdscr: curses.window) -> None:
            _setup_colors()
            stdscr.keypad(True)
            _popup(stdscr, self.translate(title_key), message.split('\n'), error=True,
                   press_key_label=self.translate(ok_key))

        curses.wrapper(_run)

    def show_confirm(self, message: str, yes_key: str, no_key: str) -> bool:
        result = [False]

        def _run(stdscr: curses.window) -> None:
            _setup_colors()
            curses.curs_set(0)
            stdscr.keypad(True)
            result[0] = _confirm_loop(stdscr, message, self.translate(yes_key), self.translate(no_key))

        curses.wrapper(_run)
        return result[0]

    def show_info(self, title: str, lines: list[str], ok_key: str) -> None:
        def _run(stdscr: curses.window) -> None:
            _setup_colors()
            stdscr.keypad(True)
            _popup(stdscr, title, lines, press_key_label=self.translate(ok_key))

        curses.wrapper(_run)
