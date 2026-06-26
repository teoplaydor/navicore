"""Global app-control hotkeys via pynput (no admin, portable — RESEARCH.md §8).

Sending key combos / mouse actions lives in actions.py (single source of truth).
"""
from __future__ import annotations

from pynput import keyboard


class GlobalHotkeys:
    """Wrap pynput GlobalHotKeys. Bindings use pynput syntax, e.g. '<ctrl>+<alt>+p'."""

    def __init__(self, bindings: dict[str, callable]):
        self._bindings = bindings
        self._listener: keyboard.GlobalHotKeys | None = None

    def start(self) -> None:
        self._listener = keyboard.GlobalHotKeys(self._bindings)
        self._listener.start()

    def stop(self) -> None:
        if self._listener is not None:
            self._listener.stop()
            self._listener = None
