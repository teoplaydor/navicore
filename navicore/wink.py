"""Single-eye wink state machine (the fragile core primitive — RESEARCH.md §5/§11).

Discriminates a deliberate held wink from a natural bilateral blink using:
  * ASYMMETRY  — one eye clearly closed while the other stays clearly open
  * HOLD       — the asymmetric state must persist ~1s (a natural blink is 0.1-0.4s)
  * BILATERAL REJECT — both eyes closing together is a blink, never a wink
  * HYSTERESIS — separate enter (closed) / exit (open) thresholds
  * SAFETY     — auto-release if the eye stays closed past a timeout

Lifecycle for the window-move mechanic:
  IDLE --(asymmetric wink held)--> ARMED  [emit 'armed': grab foreground window]
  ARMED --(winking eye re-opens)--> IDLE   [emit 'released': drop window into target]
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Side(str, Enum):
    LEFT = "left"
    RIGHT = "right"


class Phase(str, Enum):
    IDLE = "idle"
    ARMING = "arming"
    ARMED = "armed"


@dataclass
class WinkUpdate:
    phase: Phase
    side: Side | None
    event: str | None          # None | "armed" | "released" | "cancelled"
    arm_progress: float = 0.0   # 0..1 fill while holding to arm (for overlay feedback)


class WinkDetector:
    def __init__(self, cfg):
        self.cfg = cfg
        self.phase = Phase.IDLE
        self.side: Side | None = None
        self._arming_since: float | None = None
        self._armed_at: float | None = None
        self._open_since: float | None = None

    def reset(self) -> None:
        self.phase = Phase.IDLE
        self.side = None
        self._arming_since = self._armed_at = self._open_since = None

    def update(self, blink_left: float, blink_right: float, now: float) -> WinkUpdate:
        c = self.cfg
        diff = blink_left - blink_right
        bilateral = blink_left > c.blink_bilateral_reject and blink_right > c.blink_bilateral_reject
        left_wink = (blink_left > c.wink_closed_threshold and
                     blink_right < c.wink_open_threshold and diff > c.wink_diff_margin)
        right_wink = (blink_right > c.wink_closed_threshold and
                      blink_left < c.wink_open_threshold and (-diff) > c.wink_diff_margin)

        event = None
        progress = 0.0

        if self.phase == Phase.IDLE:
            if bilateral:
                pass  # natural blink — ignore
            elif left_wink:
                self.phase, self.side, self._arming_since = Phase.ARMING, Side.LEFT, now
            elif right_wink:
                self.phase, self.side, self._arming_since = Phase.ARMING, Side.RIGHT, now

        elif self.phase == Phase.ARMING:
            cur = left_wink if self.side == Side.LEFT else right_wink
            if bilateral or not cur:
                self.reset()  # wink broke before the hold completed
            else:
                started = self._arming_since if self._arming_since is not None else now
                held = now - started
                progress = min(1.0, held / max(1e-3, c.wink_hold_seconds))
                if held >= c.wink_hold_seconds:
                    self.phase, self._armed_at, self._open_since = Phase.ARMED, now, None
                    event = "armed"

        elif self.phase == Phase.ARMED:
            progress = 1.0
            winking = blink_left if self.side == Side.LEFT else blink_right
            opened = winking < c.wink_open_threshold  # hysteresis: exit on the lower threshold
            if opened:
                if self._open_since is None:
                    self._open_since = now
                elif now - self._open_since >= c.wink_release_seconds:
                    self.reset()
                    event = "released"
            else:
                self._open_since = None
            # safety: if the eye stays shut past the timeout, CANCEL the grab (do not
            # move the window — after that long the target is likely stale/accidental)
            if event is None and self._armed_at is not None and \
                    now - self._armed_at >= c.grab_timeout_seconds:
                self.reset()
                event = "cancelled"

        return WinkUpdate(self.phase, self.side, event, progress)
