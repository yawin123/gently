"""Curses form rendering: layout, draw, and the form main loop."""

import curses
from typing import Any

from ui.abstract import UIBackend, FormSpec, FieldSpec
from ui.curses.colors import _P_NORMAL, _P_ACTIVE, _P_TITLE, _P_STATUS, safe_write
from ui.curses.widgets import _fmt, inline_editor, choice_popup, list_editor, show_popup


# ---------------------------------------------------------------------------
# Layout helpers
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

def draw_form(
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
    safe_write(stdscr, 0, 0, f" {title_text}".ljust(width))
    stdscr.attroff(curses.color_pair(_P_TITLE) | curses.A_BOLD)

    row = 1
    if form.subtitle:
        subtitle_text = backend.translate(form.subtitle)
        safe_write(stdscr, row, 1, subtitle_text[:width - 2])
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
        safe_write(stdscr, row, 0,
                   f"{label_part:<{label_w}}[{val_display:<{value_w - 2}}]"[:width - 1])
        stdscr.attroff(attr)
        row += 1

    # Status bar
    stdscr.attron(curses.color_pair(_P_STATUS))
    hint = backend.translate("ui_form_hint_default")
    if _has_action(form, "delete"):
        hint = backend.translate("ui_form_hint_with_delete")
    safe_write(stdscr, height - 1, 0, hint[:width - 1].ljust(width - 1))
    stdscr.attroff(curses.color_pair(_P_STATUS))

    stdscr.refresh()


# ---------------------------------------------------------------------------
# Form main loop
# ---------------------------------------------------------------------------

def form_loop(
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

        draw_form(stdscr, form, values, current, scroll, backend)
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
            from ui.curses.screens import cycle_language
            cycle_language(stdscr, backend)
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
                new = inline_editor(stdscr, row, col, fw, initial,
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
                values[field.key] = choice_popup(stdscr, field, values[field.key], backend)
            elif field.type == "bool":
                values[field.key] = not bool(values[field.key])
            elif field.type == "list":
                values[field.key] = list_editor(stdscr, field, values[field.key], backend)
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
                show_popup(stdscr, backend.translate("ui_help_popup_title"), help_text.split('\n'),
                           press_key_label=backend.translate("ui_press_any_key"))
        elif key == curses.KEY_RESIZE:
            stdscr.clear()
