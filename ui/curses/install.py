"""Curses installation UI: live log loop and redraw logic."""

import curses
import queue
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from ui.abstract import UIBackend
from ui.curses.colors import _P_NORMAL, _P_ACTIVE, _P_TITLE, _P_STATUS, setup_colors, safe_write
from ui.curses.widgets import confirm_loop


# ---------------------------------------------------------------------------
# Install phase state
# ---------------------------------------------------------------------------

@dataclass
class InstallPhaseState:
    """Live state for a single installation phase during the curses UI."""
    key: str
    title: str
    status: str = "pending"           # pending | running | done | failed | skipped
    duration_sec: float | None = None
    log: list[str] = field(default_factory=list)
    expanded: bool = False
    log_scroll: int = 0
    log_auto_scroll: bool = True      # True = follow tail; False = user scrolled manually


# ---------------------------------------------------------------------------
# Phase helpers (shared between backend and install module)
# ---------------------------------------------------------------------------

def _init_phases(phase_keys: list[str]) -> list[InstallPhaseState]:
    """Build the initial phase list, including the synthetic 'cleanup' phase."""
    phases = [InstallPhaseState(key=pk, title=pk) for pk in phase_keys]
    phases.append(InstallPhaseState(key="cleanup", title="Cleanup"))
    return phases


def _phase_index(phases: list[InstallPhaseState]) -> dict[str, int]:
    return {p.key: i for i, p in enumerate(phases)}


# ---------------------------------------------------------------------------
# Live install loop
# ---------------------------------------------------------------------------

def install_live_loop(
    stdscr: curses.window,
    *,
    phases: list[InstallPhaseState],
    phase_index: dict[str, int],
    msg_queue: queue.Queue,
    backend: UIBackend,
    finished_ref: list[bool],       # [0] — mutable bool
    selected_ref: list[int],        # [0] — mutable int
    scroll_ref: list[int],          # [0] — mutable int
    abort_event: threading.Event | None = None,
) -> None:
    """Live installation UI driven by *msg_queue*.

    Runs inside ``curses.wrapper`` on whichever thread owns the curses session
    (the main thread when ``prepare_install()`` is used, or a daemon thread
    in standalone mode).
    """
    setup_colors()
    curses.curs_set(0)
    stdscr.keypad(True)
    # timeout(50) → waits up to 50 ms for a key so ncurses can assemble
    # multi-byte sequences (KEY_NPAGE, KEY_PPAGE) correctly.  This is fast
    # enough for a responsive UI while preventing the ESC byte from being
    # misinterpreted as a standalone abort key.
    stdscr.timeout(50)
    needs_redraw = True
    auto_select = True

    while True:
        # ── Drain incoming queue messages ───────────────────
        try:
            while True:
                msg = msg_queue.get_nowait()
                kind = msg[0]
                if kind == "log":
                    _, phase_key, message = msg
                    idx = phase_index.get(phase_key)
                    if idx is not None:
                        ph = phases[idx]
                        if ph.status == "pending":
                            ph.status = "running"
                            ph.expanded = True
                            if auto_select:
                                selected_ref[0] = idx
                        ph.log.append(message)
                        needs_redraw = True
                elif kind == "done":
                    _, report = msg
                    for phase_result in report.phases:
                        idx2 = phase_index.get(phase_result.key)
                        if idx2 is not None:
                            ph = phases[idx2]
                            ph.status = "done" if phase_result.status == "ok" else "failed"
                            ph.duration_sec = phase_result.duration_sec
                            if phase_result.error:
                                ph.log.append(f"ERROR: {phase_result.error}")
                    for ph in phases:
                        if ph.status in ("pending", "running"):
                            if ph.key == "cleanup":
                                if any("WARNING" in line for line in ph.log):
                                    ph.status = "failed"
                                else:
                                    ph.status = "done"
                            else:
                                ph.status = "skipped"
                        # Pin every phase to the bottom so the final redraw
                        # shows the last lines, not a stale mid-log position.
                        ph.log_scroll = len(ph.log)
                    finished_ref[0] = True
                    needs_redraw = True
                elif kind == "confirm":
                    _, message, yes_label, no_label, resp_q = msg
                    answer = confirm_loop(stdscr, message, yes_label, no_label)
                    resp_q.put(answer)
                    needs_redraw = True
        except queue.Empty:
            pass

        # ── Keyboard input ───────────────────────────────────
        selected = selected_ref[0]
        try:
            key = stdscr.getch()
        except KeyboardInterrupt:
            return

        if key != -1:
            needs_redraw = True
            auto_select = False

        if not finished_ref[0] and key in (ord('q'), ord('Q'), 27):
            # Abort during installation — signal the abort event so the
            # installer loop stops after the current phase and runs cleanup.
            # Stay in the UI so the user can watch cleanup progress.
            if abort_event is not None:
                abort_event.set()
        elif finished_ref[0] and key in (ord('q'), ord('Q'), 27):
            return
        elif key == curses.KEY_DOWN:
            selected = min(len(phases) - 1, selected + 1)
        elif key == curses.KEY_UP:
            selected = max(0, selected - 1)
        elif key in (curses.KEY_ENTER, 10, 13, ord(' ')):
            ph = phases[selected]
            if ph.status != "pending":
                ph.expanded = not ph.expanded
                ph.log_scroll = max(0, len(ph.log) - 1) if ph.expanded else 0
                ph.log_auto_scroll = True
        elif key == curses.KEY_RESIZE:
            pass
        elif key == curses.KEY_NPAGE:
            ph = phases[selected]
            if ph.expanded and ph.log:
                ph.log_scroll = min(len(ph.log) - 1, ph.log_scroll + 5)
                if ph.log_scroll >= len(ph.log) - 1:
                    ph.log_auto_scroll = True
                else:
                    ph.log_auto_scroll = False
        elif key == curses.KEY_PPAGE:
            ph = phases[selected]
            if ph.expanded and ph.log:
                ph.log_scroll = max(0, ph.log_scroll - 5)
                ph.log_auto_scroll = False

        selected_ref[0] = selected

        # ── Redraw ───────────────────────────────────────────
        if needs_redraw:
            _install_redraw(stdscr, phases, finished_ref[0], selected, scroll_ref, backend)
            needs_redraw = False
        elif not finished_ref[0]:
            time.sleep(0.05)  # ~20 fps while installing


# ---------------------------------------------------------------------------
# Redraw
# ---------------------------------------------------------------------------

def _install_redraw(
    stdscr: curses.window,
    phases: list[InstallPhaseState],
    finished: bool,
    selected: int,
    scroll_ref: list[int],
    backend: Any,
) -> None:
    """Redraw the interactive installation log screen."""
    try:
        scroll = scroll_ref[0]
        height, width = stdscr.getmaxyx()
        stdscr.clear()

        # Title bar
        if finished:
            title = backend.translate("ui_install_title_done")
        else:
            title = backend.translate("ui_install_title_running")
        stdscr.attron(curses.color_pair(_P_TITLE) | curses.A_BOLD)
        safe_write(stdscr, 0, 0, title.ljust(width))
        stdscr.attroff(curses.color_pair(_P_TITLE) | curses.A_BOLD)

        visible_top = 1
        visible_bot = height - 2

        # Ensure the selected phase is visible, with priority for expanded logs.
        row = visible_top
        sel_visible = False
        for test_idx in range(scroll, len(phases)):
            if test_idx == selected:
                sel_visible = True
            ph = phases[test_idx]
            row += 1  # header
            if ph.expanded and ph.log and test_idx < selected:
                available = visible_bot - row + 1
                log_h = max(1, min(len(ph.log), available))
                row += log_h
            if row > visible_bot:
                break

        # If the selected phase is expanded, pin its header at the top of the
        # visible area so its log gets maximum room.  Otherwise just ensure it
        # stays in view.
        if phases[selected].expanded:
            # Put the selected phase header at visible_top + 1.
            scroll = selected
        elif not sel_visible or row > visible_bot or selected < scroll:
            scroll = selected

        scroll_ref[0] = scroll

        # ── Render from scroll forward ────────────────
        row = visible_top
        for i in range(scroll, len(phases)):
            if row > visible_bot:
                break
            ph = phases[i]

            # Status icon
            icon_map = {
                "pending": " ",
                "running": "▶",
                "done": "✓",
                "skipped": "–",
            }
            icon = icon_map.get(ph.status, "✗")
            dur_str = f" ({ph.duration_sec:.1f}s)" if ph.duration_sec is not None else ""
            active = i == selected
            attr = curses.color_pair(_P_ACTIVE) if active else curses.color_pair(_P_NORMAL)
            marker = "▶ " if active else "  "
            line = f"{marker}{icon} {ph.title}{dur_str}"
            stdscr.attron(attr)
            safe_write(stdscr, row, 1, line[:width - 2])
            stdscr.attroff(attr)
            row += 1

            # Expanded log
            if ph.expanded and ph.log:
                log_lines = ph.log
                available = visible_bot - row + 1
                log_h = max(1, min(len(log_lines), available))
                if ph.status == "running":
                    # While installing, auto-follow the tail unless the user
                    # scrolled manually.  If new lines arrived and we are in
                    # auto mode, pin to the bottom.
                    new_total = len(log_lines)
                    if ph.log_auto_scroll:
                        log_scroll = max(0, new_total - log_h)
                    else:
                        log_scroll = max(0, min(ph.log_scroll, max(0, new_total - log_h)))
                else:
                    log_scroll = max(0, min(ph.log_scroll, max(0, len(log_lines) - log_h)))
                ph.log_scroll = log_scroll
                for j in range(log_h):
                    log_idx = log_scroll + j
                    if log_idx < len(log_lines):
                        log_line = log_lines[log_idx]
                        is_last_log = (log_idx == len(log_lines) - 1) and ph.status == "running"
                        line_attr = curses.A_BOLD if is_last_log else curses.A_NORMAL
                        safe_write(stdscr, row, 4, f"  {log_line}"[:width - 6], line_attr)
                    row += 1

        # Status bar
        hint = backend.translate("ui_install_hint")
        safe_write(stdscr, height - 1, 0, hint.ljust(width - 1), curses.color_pair(_P_STATUS))
        stdscr.refresh()
    except Exception:
        pass
