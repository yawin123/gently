"""Curses UI backend — public class with methods delegating to submodules."""

from __future__ import annotations

import curses
import os
import queue
import threading
from typing import Any

from ui.abstract import UIBackend, FormSpec
from ui.curses.colors import setup_colors
from ui.curses.forms import form_loop
from ui.curses.install import (
    InstallPhaseState,
    _init_phases,
    _phase_index,
    install_live_loop,
)
from ui.curses.screens import (
    section_menu_loop,
    summary_loop,
    subsection_loop,
)
from ui.curses.widgets import confirm_loop, show_popup

# Reduce the ESC-key disambiguation timeout from the ncurses default (1000 ms)
# to 25 ms. Without this, every Esc keypress has a noticeable lag because the
# terminal waits to see if ESC is the start of a multi-byte escape sequence.
os.environ.setdefault("ESCDELAY", "25")


class CursesBackend(UIBackend):
    """ncurses-based terminal UI backend."""

    def __init__(self) -> None:
        # Install-related state — all initialised here so hasattr() is never needed.
        self._install_phases: list[InstallPhaseState] = []
        self._install_phase_index: dict[str, int] = {}
        self._install_queue: queue.Queue | None = None
        self._install_finished: list[bool] = [False]
        self._install_selected: list[int] = [0]
        self._install_scroll: list[int] = [0]
        self._install_ui_thread: threading.Thread | None = None
        self._abort_event: threading.Event | None = None

    # ── Public UI methods ──────────────────────────────────

    def show_form(self, form: FormSpec) -> dict[str, Any] | None:
        values = {f.key: f.default for f in form.fields}
        result: list[dict[str, Any] | None] = [values]

        def _run(stdscr: curses.window) -> None:
            setup_colors()
            curses.curs_set(0)
            stdscr.keypad(True)
            form_loop(stdscr, form, values, result, self)

        curses.wrapper(_run)
        return result[0]

    def show_subsection(self, title: str, items: list[tuple[str, dict]]) -> str:
        result = ["done"]

        def _run(stdscr: curses.window) -> None:
            setup_colors()
            curses.curs_set(0)
            stdscr.keypad(True)
            subsection_loop(stdscr, title, items, result, self)

        curses.wrapper(_run)
        return result[0]

    def show_section_menu(
        self,
        sections: list[tuple[str, str, bool]],
        all_complete: bool,
    ) -> str:
        result = ["save_and_exit"]

        def _run(stdscr: curses.window) -> None:
            setup_colors()
            curses.curs_set(0)
            stdscr.keypad(True)
            section_menu_loop(stdscr, sections, all_complete, result, self)

        curses.wrapper(_run)
        return result[0]

    def show_summary(self, sections: list[tuple[str, dict]]) -> str:
        result = ["save_and_exit"]

        def _run(stdscr: curses.window) -> None:
            setup_colors()
            curses.curs_set(0)
            stdscr.keypad(True)
            summary_loop(stdscr, sections, result, self)

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
        self._install_phases = _init_phases([p.key for p in phases])
        # Override titles from the phase definitions.
        for state, phase in zip(self._install_phases, phases):
            state.title = phase.title
        self._install_phase_index = _phase_index(self._install_phases)
        self._install_queue = queue.Queue()
        self._install_finished = [False]
        self._install_selected = [0]
        self._install_scroll = [0]
        self._install_ui_thread = None
        self._abort_event = threading.Event()

    def run_install_ui(self) -> None:
        """Block on the calling (main) thread driving the curses installation UI."""
        try:
            curses.wrapper(self._run_install_ui)
        except KeyboardInterrupt:
            raise

    def _run_install_ui(self, stdscr: curses.window) -> None:
        install_live_loop(
            stdscr,
            phases=self._install_phases,
            phase_index=self._install_phase_index,
            msg_queue=self._install_queue,
            backend=self,
            finished_ref=self._install_finished,
            selected_ref=self._install_selected,
            scroll_ref=self._install_scroll,
            abort_event=self._abort_event,
        )

    def install_progress_begin(self, phase_keys: list[str]) -> None:
        """Initialise phase data. No-op when prepare_install() was already called.

        Standalone mode: if prepare_install() was not called beforehand, the
        installation is assumed to run on the main thread; a background UI thread
        is started so curses does not conflict with the calling thread.
        """
        if self._install_queue is not None:
            return

        self._install_phases = _init_phases(phase_keys)
        self._install_phase_index = _phase_index(self._install_phases)
        self._install_queue = queue.Queue()
        self._install_finished = [False]
        self._install_selected = [0]
        self._install_scroll = [0]
        self._abort_event = threading.Event()
        self._install_ui_thread = threading.Thread(
            target=lambda: curses.wrapper(self._run_install_ui),
            daemon=True,
            name="gently-install-ui",
        )
        self._install_ui_thread.start()

    def install_progress_update(self, phase_key: str, message: str) -> None:
        """Send a log line to the live UI thread via the queue."""
        if self._install_queue is not None:
            self._install_queue.put(("log", phase_key, message))

    def install_progress_end(self, report: Any) -> None:
        """Signal the UI thread that installation is done, then wait for the user."""
        if self._install_queue is not None:
            self._install_queue.put(("done", report))
        if self._install_ui_thread is not None:
            self._install_ui_thread.join()

    # ── Dialogs ────────────────────────────────────────────

    def show_error(self, title_key: str, message: str, ok_key: str) -> None:
        def _run(stdscr: curses.window) -> None:
            setup_colors()
            stdscr.keypad(True)
            show_popup(stdscr, self.translate(title_key), message.split('\n'), error=True,
                       press_key_label=self.translate(ok_key))

        curses.wrapper(_run)

    def show_confirm(self, message: str, yes_key: str, no_key: str) -> bool:
        yes_label = self.translate(yes_key)
        no_label = self.translate(no_key)

        # If an active install session is running, route through the queue
        # so the dialog runs on the curses-owning thread.
        if self._install_queue is not None:
            resp_q: queue.Queue[bool] = queue.Queue()
            self._install_queue.put(("confirm", message, yes_label, no_label, resp_q))
            return resp_q.get()

        result = [False]

        def _run(stdscr: curses.window) -> None:
            setup_colors()
            curses.curs_set(0)
            stdscr.keypad(True)
            result[0] = confirm_loop(stdscr, message, yes_label, no_label)

        curses.wrapper(_run)
        return result[0]

    def show_info(self, title: str, lines: list[str], ok_key: str) -> None:
        def _run(stdscr: curses.window) -> None:
            setup_colors()
            stdscr.keypad(True)
            show_popup(stdscr, title, lines, press_key_label=self.translate(ok_key))

        curses.wrapper(_run)
