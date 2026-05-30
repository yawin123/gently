"""Curses screens: section menu, summary, subsection, and language cycling."""

import curses
from typing import Any

from ui.abstract import UIBackend, FieldSpec
from ui.curses.colors import _P_NORMAL, _P_ACTIVE, _P_TITLE, _P_STATUS, safe_write
from ui.curses.widgets import choice_popup


# ---------------------------------------------------------------------------
# Section menu
# ---------------------------------------------------------------------------

def section_menu_loop(
    stdscr: curses.window,
    sections: list[tuple[str, str, bool]],  # (key, name, is_complete)
    all_complete: bool,
    result: list,
    backend: UIBackend,
) -> None:
    selected = 0
    scroll = 0

    while True:
        height, width = stdscr.getmaxyx()
        content_h = height - 3  # title bar + status bar
        stdscr.erase()

        # Title bar
        title = backend.translate("ui_menu_title")
        stdscr.attron(curses.color_pair(_P_TITLE) | curses.A_BOLD)
        safe_write(stdscr, 0, 0, title.ljust(width))
        stdscr.attroff(curses.color_pair(_P_TITLE) | curses.A_BOLD)

        # Keep selected item visible
        if selected < scroll:
            scroll = selected
        elif selected >= scroll + content_h:
            scroll = selected - content_h + 1
        scroll = max(0, min(scroll, len(sections) - content_h))

        # Section list (scroll-aware)
        visible_end = min(scroll + content_h, len(sections))
        for i in range(scroll, visible_end):
            _key, name, complete = sections[i]
            row = 2 + (i - scroll)
            icon = "\u2713" if complete else " "
            line = f"  {icon}  {name}"
            if i == selected:
                safe_write(stdscr, row, 0, line.ljust(width - 1), curses.color_pair(_P_ACTIVE))
            else:
                safe_write(stdscr, row, 0, line[:width - 1])

        # Status bar
        hint = backend.translate("ui_menu_hint")
        if all_complete:
            hint += backend.translate("ui_menu_hint_install_suffix")
        stdscr.attron(curses.color_pair(_P_STATUS))
        safe_write(stdscr, height - 1, 0, hint[:width - 1].ljust(width - 1))
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
            cycle_language(stdscr, backend)
        elif key == 27:  # Esc
            backend.interrupt()
            return
        elif key == curses.KEY_RESIZE:
            stdscr.clear()


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

def summary_loop(
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
        safe_write(stdscr, 0, 0, backend.translate("ui_summary_title").ljust(width))
        stdscr.attroff(curses.color_pair(_P_TITLE) | curses.A_BOLD)

        for i, (ltype, _lkey, ltext) in enumerate(lines[scroll:scroll + content_h]):
            row = i + 1
            if ltype == "header":
                safe_write(stdscr, row, 0, f"[{ltext}]"[:width - 1], curses.A_BOLD)
            elif ltype == "field":
                safe_write(stdscr, row, 0, ltext[:width - 1])

        # Action bar
        col = 2
        for i, (label, _key) in enumerate(actions):
            attr = curses.color_pair(_P_ACTIVE) if i == sel else curses.color_pair(_P_NORMAL)
            stdscr.attron(attr)
            safe_write(stdscr, height - 2, col, f" {label} ")
            stdscr.attroff(attr)
            col += len(label) + 4

        stdscr.attron(curses.color_pair(_P_STATUS))
        hint = backend.translate("ui_summary_hint")
        safe_write(stdscr, height - 1, 0,
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
            cycle_language(stdscr, backend)
        elif key in (curses.KEY_RIGHT, ord('\t')):
            sel = (sel + 1) % len(actions)
        elif key in (curses.KEY_LEFT, curses.KEY_BTAB):
            sel = (sel - 1) % len(actions)
        elif key in (curses.KEY_ENTER, 10, 13):
            action_key = actions[sel][1]
            if action_key == "cancel" and section_keys:
                chosen = choice_popup(
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
                chosen = choice_popup(
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
# Subsection loop (e.g. partitions)
# ---------------------------------------------------------------------------

def subsection_loop(
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
        safe_write(stdscr, 0, 0, f" {title}".ljust(width))
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
            safe_write(stdscr, row, 0, f"{marker}{text}"[:width - 1])
            stdscr.attroff(attr)

        stdscr.attron(curses.color_pair(_P_STATUS))
        hint = backend.translate("ui_subsection_hint")
        safe_write(stdscr, height - 1, 0,
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
            cycle_language(stdscr, backend)
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

def cycle_language(stdscr: curses.window, backend: UIBackend) -> None:
    langs = backend.available_languages()
    if len(langs) < 2:
        return
    current = backend.current_language()
    chosen = choice_popup(
        stdscr,
        FieldSpec(key="lang", label=backend.translate("ui_language_label"), type="choice", options=langs),
        current,
        backend,
    )
    if chosen is not None:
        backend.reload_language(chosen)
