"""Head-driven app switcher — "gesture Alt-Tab".

Hold a configured static gesture (default Victory) -> the NATIVE Windows Alt-Tab
switcher opens (we hold a synthetic Alt and send Tab); turning the head moves the
selection like a cursor (yaw quantized into steps, Tab / Shift+Tab); releasing the
gesture releases Alt -> Windows activates the selected window. Face loss or timeout
cancels with Esc so Alt can never stick.

Driving the real Alt-Tab gives the familiar UI with live previews for free and
avoids all SetForegroundWindow restrictions (Windows performs the activation).
"""
from __future__ import annotations

from enum import Enum

from pynput import keyboard


class SwState(str, Enum):
    IDLE = "idle"
    ARMING = "arming"
    OPEN = "open"


class AppSwitcher:
    def __init__(self, cfg, send=None):
        self.cfg = cfg
        self._kb = keyboard.Controller()
        self.send = send or self._default_send   # injectable for tests
        self.state = SwState.IDLE
        self._arming_since = 0.0
        self._opened_at = 0.0
        self._neutral_yaw = 0.0
        self._index = 0          # steps relative to the initial Alt+Tab selection
        self._last_step_t = 0.0
        self._last_gesture_t = 0.0
        self._face_lost_since: float | None = None
        self.last_action = ""    # debug

    # ---- key plumbing ----
    def _default_send(self, what: str) -> None:
        k = self._kb
        if what == "alt_down":
            k.press(keyboard.Key.alt)
        elif what == "alt_up":
            k.release(keyboard.Key.alt)
        elif what == "tab":
            k.press(keyboard.Key.tab)
            k.release(keyboard.Key.tab)
        elif what == "shift_tab":
            k.press(keyboard.Key.shift)
            k.press(keyboard.Key.tab)
            k.release(keyboard.Key.tab)
            k.release(keyboard.Key.shift)
        elif what == "esc":
            k.press(keyboard.Key.esc)
            k.release(keyboard.Key.esc)

    # ---- state ----
    @property
    def active(self) -> bool:
        return self.state == SwState.OPEN

    def abort(self) -> None:
        """Cancel without activating anything (pause/calibration/stall/shutdown)."""
        if self.state == SwState.OPEN:
            self.send("esc")
            self.send("alt_up")
            self.last_action = "cancelled"
        self.state = SwState.IDLE
        self._face_lost_since = None

    def update(self, gesture_name: str, score: float,
               face_present: bool, yaw: float, now: float) -> None:
        cfg = self.cfg
        if not getattr(cfg, "switcher_enabled", False):
            if self.state != SwState.IDLE:
                self.abort()
            return
        showing = (gesture_name == cfg.switcher_gesture
                   and score >= cfg.gesture_min_confidence)
        if showing:
            self._last_gesture_t = now

        if self.state == SwState.IDLE:
            if showing:
                self.state = SwState.ARMING
                self._arming_since = now

        elif self.state == SwState.ARMING:
            if not showing:
                self.state = SwState.IDLE
            elif now - self._arming_since >= cfg.switcher_hold_seconds:
                self.state = SwState.OPEN
                self._opened_at = now
                self._neutral_yaw = yaw
                self._index = 0
                self._last_step_t = now
                self._face_lost_since = None
                self.send("alt_down")
                self.send("tab")          # opens the switcher on the previous app
                self.last_action = "opened"

        elif self.state == SwState.OPEN:
            # safety: timeout
            if now - self._opened_at >= cfg.switcher_timeout_seconds:
                self.abort()
                return
            # safety: face lost
            if not face_present:
                if self._face_lost_since is None:
                    self._face_lost_since = now
                elif now - self._face_lost_since >= 0.7:
                    self.abort()
                    return
                yaw = self._neutral_yaw + self._index * cfg.switcher_step_deg
            else:
                self._face_lost_since = None

            # head yaw -> target selection step (cursor-like, quantized)
            target = round((yaw - self._neutral_yaw) / max(1e-3, cfg.switcher_step_deg))
            target = max(-1, min(int(cfg.switcher_max_steps), target))
            if target != self._index and now - self._last_step_t >= 0.08:
                if target > self._index:
                    self.send("tab")
                    self._index += 1
                else:
                    self.send("shift_tab")
                    self._index -= 1
                self._last_step_t = now
                self.last_action = f"step {self._index:+d}"

            # release the gesture (with a small grace for classifier flicker) -> confirm
            if now - self._last_gesture_t >= cfg.switcher_release_grace:
                self.send("alt_up")
                self.state = SwState.IDLE
                self.last_action = "activated"
