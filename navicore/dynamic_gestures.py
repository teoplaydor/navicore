"""Dynamic hand gestures from landmark trajectories (RESEARCH.md §13.1).

The MediaPipe Gesture Recognizer is single-frame/static, but every result already
carries 21 hand landmarks — swipes and pinch are pure trajectory heuristics on top:

  * SWIPE  — net wrist (landmark 0) travel over a short window, dominant-axis,
             velocity-gated, debounced; optionally gated on a recently seen
             Open_Palm pose (touchscreen-like "open hand swipe", cuts false
             positives from reaching for the mouse/coffee).
  * PINCH  — thumb-tip(4) to index-tip(8) distance normalized by hand scale
             (wrist(0) to middle-MCP(9)), so it is camera-distance invariant.
             Fires once per `pinch_step` ratio change — naturally repeat-fires
             while the user keeps spreading/closing the fingers (zoom in/out).

Event names match config.DYNAMIC_GESTURE_NAMES:
  Swipe_Left / Swipe_Right / Swipe_Up / Swipe_Down / Pinch_Out / Pinch_In
"""
from __future__ import annotations

import math
from collections import deque

_WRIST, _THUMB_TIP, _INDEX_TIP, _MIDDLE_MCP = 0, 4, 8, 9


class DynamicGestureDetector:
    def __init__(self, cfg, mirror: bool = True):
        self.cfg = cfg
        self.mirror = mirror
        self._track: deque[tuple[float, float, float]] = deque()  # (t, x, y) wrist
        self._pinch: deque[tuple[float, float]] = deque()          # (t, ratio)
        self._pinch_anchor: float | None = None
        self._last_fire_t = -1e9   # never fired yet — must not debounce the first event
        self._last_palm_t = -1e9
        self._fired_this_track = False
        # debug
        self.last_event = ""
        self.pinch_ratio = 0.0

    def reset(self) -> None:
        self._track.clear()
        self._pinch.clear()
        self._pinch_anchor = None
        self._fired_this_track = False

    # ---- helpers ----
    @staticmethod
    def _dist(a, b) -> float:
        return math.hypot(a.x - b.x, a.y - b.y)

    def _note_palm(self, gesture_name: str, now: float) -> None:
        if gesture_name == "Open_Palm":
            self._last_palm_t = now

    def _palm_ok(self, now: float) -> bool:
        if not self.cfg.swipe_require_palm:
            return True
        return (now - self._last_palm_t) < 0.8

    # ---- main ----
    def update(self, landmarks, gesture_name: str, now: float) -> str | None:
        """Feed one frame's hand landmarks (or None when no hand). Returns an event
        name from DYNAMIC_GESTURE_NAMES or None."""
        if not landmarks:
            self.reset()
            return None
        self._note_palm(gesture_name, now)

        wrist = landmarks[_WRIST]
        x = wrist.x if self.mirror else 1.0 - wrist.x  # user-space: +x = user's right
        y = wrist.y
        win = self.cfg.swipe_window_seconds
        self._track.append((now, x, y))
        while self._track and now - self._track[0][0] > win:
            self._track.popleft()
            self._fired_this_track = False  # window slid past the fired burst

        ev = self._detect_pinch(landmarks, now)
        if ev is None:
            ev = self._detect_swipe(now)
        if ev is not None:
            self._last_fire_t = now
            self.last_event = ev
        return ev

    def _debounced(self, now: float) -> bool:
        return (now - self._last_fire_t) < self.cfg.dynamic_debounce_seconds

    def _detect_swipe(self, now: float) -> str | None:
        if len(self._track) < 4 or self._fired_this_track or self._debounced(now):
            return None
        t0, x0, y0 = self._track[0]
        t1, x1, y1 = self._track[-1]
        dt = t1 - t0
        if dt < 0.1:
            return None
        dx, dy = x1 - x0, y1 - y0
        adx, ady = abs(dx), abs(dy)
        travel = max(adx, ady)
        if travel < self.cfg.swipe_min_travel:
            return None
        speed = travel / dt
        if speed < self.cfg.swipe_min_travel / max(0.05, self.cfg.swipe_window_seconds):
            return None
        if not self._palm_ok(now):
            return None
        if adx >= 2.0 * ady:        # dominant horizontal
            ev = "Swipe_Right" if dx > 0 else "Swipe_Left"
        elif ady >= 2.0 * adx:      # dominant vertical (image y grows downward)
            ev = "Swipe_Down" if dy > 0 else "Swipe_Up"
        else:
            return None             # diagonal — ambiguous, ignore
        self._fired_this_track = True
        self._track.clear()
        return ev

    def _detect_pinch(self, landmarks, now: float) -> str | None:
        scale = self._dist(landmarks[_WRIST], landmarks[_MIDDLE_MCP])
        if scale < 1e-4:
            return None
        ratio = self._dist(landmarks[_THUMB_TIP], landmarks[_INDEX_TIP]) / scale
        self.pinch_ratio = ratio
        self._pinch.append((now, ratio))
        while self._pinch and now - self._pinch[0][0] > 0.8:
            self._pinch.popleft()

        # suppress pinch while the wrist is sweeping (that's a swipe, not a zoom)
        if len(self._track) >= 2:
            t0, x0, y0 = self._track[0]
            t1, x1, y1 = self._track[-1]
            if max(abs(x1 - x0), abs(y1 - y0)) > 0.5 * self.cfg.swipe_min_travel:
                self._pinch_anchor = None
                return None

        if self._pinch_anchor is None:
            if len(self._pinch) >= 3:   # settle before anchoring
                self._pinch_anchor = ratio
            return None
        step = self.cfg.pinch_step
        if ratio - self._pinch_anchor >= step:
            self._pinch_anchor = ratio
            return "Pinch_Out"
        if self._pinch_anchor - ratio >= step:
            self._pinch_anchor = ratio
            return "Pinch_In"
        return None
