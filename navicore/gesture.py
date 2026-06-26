"""Hand-gesture controller via MediaPipe Gesture Recognizer (RESEARCH.md §6).

Each of the 8 built-in gestures can be bound (in config.gesture_bindings) to an action:
  * action     : a key combo / key / mouse spec (see actions.py), e.g. "ctrl+alt+doubleclick"
  * hold_seconds: how long the gesture must be shown before it activates
  * mode "tap"  : fire the action once when the hold completes (re-arms when the pose ends)
  * mode "hold" : press-and-hold the keys while the pose is shown; release when it ends
"""
from __future__ import annotations

import cv2
import numpy as np
import mediapipe as mp
from mediapipe.tasks import python as mp_python
from mediapipe.tasks.python import vision

from . import actions
from .dynamic_gestures import DynamicGestureDetector


class GestureController:
    def __init__(self, model_path: str, cfg):
        self.cfg = cfg
        self.dyn = DynamicGestureDetector(cfg, mirror=cfg.mirror)
        self.dynamic_enabled = True   # engine clears this while the app switcher is open
        base = mp_python.BaseOptions(model_asset_path=model_path)
        opts = vision.GestureRecognizerOptions(
            base_options=base,
            running_mode=vision.RunningMode.VIDEO,
            num_hands=1,
        )
        self._rec = vision.GestureRecognizer.create_from_options(opts)
        self._ts = 0
        self._invalid: set[str] = set()
        # active-gesture tracking
        self._active: str | None = None      # gesture currently being timed
        self._active_since: float = 0.0
        self._fired = False                   # tap already fired for this show
        self._held: actions.HeldAction | None = None
        self._held_gesture: str | None = None
        # debug
        self.last_gesture = ""
        self.last_score = 0.0
        self.last_fired = ""
        self.revalidate()   # must run after _held/_active exist (it calls release_all)

    def revalidate(self) -> None:
        """Re-check binding action specs (called at init and after config hot-reload).
        Bindings with typo'd specs are refused — a bad modifier would otherwise degrade
        to injecting a bare keystroke into the focused app."""
        self.release_all()   # mode/action may have changed under a held action
        self._invalid = set()
        # the app-switcher gesture is reserved: its tap/hold binding must not also fire
        if getattr(self.cfg, "switcher_enabled", False):
            self._invalid.add(self.cfg.switcher_gesture)
        for name, b in (self.cfg.gesture_bindings or {}).items():
            if isinstance(b, dict) and b.get("enabled"):
                bad = actions.validate_spec(b.get("action", ""))
                if bad:
                    print(f"[gesture] binding '{name}': unknown tokens {bad} "
                          f"in action '{b.get('action')}' — binding disabled")
                    self._invalid.add(name)

    def _binding(self, name: str):
        if name in self._invalid or name == "None":
            return None
        b = self.cfg.gesture_bindings.get(name)
        if b and b.get("enabled"):
            return b
        return None

    def process(self, bgr_frame: np.ndarray, now: float, timestamp_ms: int | None = None) -> None:
        if timestamp_ms is None or timestamp_ms <= self._ts:
            timestamp_ms = self._ts + 1
        self._ts = timestamp_ms

        rgb = cv2.cvtColor(bgr_frame, cv2.COLOR_BGR2RGB)
        mp_img = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
        res = self._rec.recognize_for_video(mp_img, timestamp_ms)

        name, score = "", 0.0
        if res.gestures and res.gestures[0]:
            top = res.gestures[0][0]
            name, score = top.category_name, float(top.score)
        self.last_gesture, self.last_score = name, score

        # ---- dynamic gestures (swipes / pinch) over the hand landmarks ----
        lms = res.hand_landmarks[0] if res.hand_landmarks else None
        dyn_ev = self.dyn.update(lms, name, now)
        if dyn_ev is not None and self.dynamic_enabled:
            b = self._binding(dyn_ev)
            if b is not None:   # dynamic gestures always fire as a tap
                spec = b.get("action", "")
                try:
                    actions.perform_tap(spec)
                    self.last_fired = f"{dyn_ev}: {spec}"
                except Exception as exc:
                    print(f"[gesture] action '{spec}' failed: {exc}")

        # effective gesture = a bound, enabled gesture above the confidence floor
        eff = name if (score >= self.cfg.gesture_min_confidence and self._binding(name)) else None

        # release a held action if its gesture is no longer effective
        if self._held is not None and self._held_gesture != eff:
            self._held.release()
            self._held = None
            self._held_gesture = None

        if eff != self._active:
            self._active = eff
            self._active_since = now
            self._fired = False

        if eff is None:
            return
        b = self._binding(eff)
        if b is None or now - self._active_since < float(b.get("hold_seconds", 0.5)):
            return

        mode = b.get("mode", "tap")
        spec = b.get("action", "")
        if mode == "hold":
            if self._held is None:
                self._held = actions.start_hold(spec)
                self._held_gesture = eff
                self.last_fired = f"{eff}: HOLD {spec}"
        else:  # tap
            if not self._fired:
                self._fired = True
                try:
                    actions.perform_tap(spec)
                    self.last_fired = f"{eff}: {spec}"
                except Exception as exc:
                    print(f"[gesture] action '{spec}' failed: {exc}")

    def release_all(self) -> None:
        if self._held is not None:
            self._held.release()
            self._held = None
            self._held_gesture = None

    def close(self) -> None:
        self.release_all()
        try:
            self._rec.close()
        except Exception:
            pass
