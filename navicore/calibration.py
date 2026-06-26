"""Guided directional head-pose calibration.

Walks the user through 5 poses and learns the actual yaw/pitch endpoints, so the
mapping handles whatever sign the head-pose estimator produces (fixes the "look up ->
zone goes down" inversion) and adapts the range to the real monitor layout.

The engine drives this: it feeds face frames and renders the returned prompt.
"""
from __future__ import annotations

import numpy as np

# (key, instruction) in order
STEPS = [
    ("neutral", "Look STRAIGHT at the center of your main screen"),
    ("up",      "Tilt your head UP and hold"),
    ("down",    "Tilt your head DOWN and hold"),
    ("left",    "Turn your head LEFT (toward the leftmost screen)"),
    ("right",   "Turn your head RIGHT (toward the rightmost screen)"),
]


class Calibrator:
    def __init__(self, cfg, save_path: str | None = None):
        self.cfg = cfg
        self.save_path = save_path   # None -> Config.save() default (the real config file)
        self._active = False
        self._step = 0
        self._phase = "settle"          # "settle" then "collect"
        self._phase_until = 0.0
        self._buf: list[tuple[float, float]] = []
        self._results: dict[str, tuple[float, float]] = {}

    @property
    def active(self) -> bool:
        return self._active

    def start(self, now: float) -> None:
        self._active = True
        self._step = 0
        self._phase = "settle"
        self._phase_until = now + self.cfg.cal_settle_seconds
        self._buf = []
        self._results = {}

    def cancel(self) -> None:
        self._active = False

    def update(self, face, now: float) -> dict | None:
        """Return {prompt, sub, progress} while running, or None when not active."""
        if not self._active:
            return None
        key, instr = STEPS[self._step]

        if self._phase == "settle":
            remaining = max(0.0, self._phase_until - now)
            if now >= self._phase_until:
                self._phase = "collect"
                self._phase_until = now + self.cfg.cal_collect_seconds
                self._buf = []
            return {"prompt": f"[{self._step + 1}/{len(STEPS)}] {instr}",
                    "sub": f"get ready... {remaining:0.1f}s",
                    "progress": 0.0}

        # collect
        if face.present:
            self._buf.append((face.yaw, face.pitch))
        total = self.cfg.cal_collect_seconds
        elapsed = total - max(0.0, self._phase_until - now)
        progress = min(1.0, elapsed / max(1e-3, total))

        if now >= self._phase_until:
            if self._buf:
                ys = [y for y, _ in self._buf]
                ps = [p for _, p in self._buf]
                self._results[key] = (float(np.median(ys)), float(np.median(ps)))
            else:
                self._results[key] = (self.cfg.neutral_yaw_deg, self.cfg.neutral_pitch_deg)
            self._step += 1
            if self._step >= len(STEPS):
                self._finish()
                self._active = False
                return None
            self._phase = "settle"
            self._phase_until = now + self.cfg.cal_settle_seconds

        return {"prompt": f"[{self._step + 1}/{len(STEPS)}] {instr}",
                "sub": "hold the pose...", "progress": progress}

    def _finish(self) -> None:
        cfg, r = self.cfg, self._results
        n = r.get("neutral")
        if n:
            cfg.neutral_yaw_deg, cfg.neutral_pitch_deg = n
        if "up" in r and "down" in r:
            cfg.cal_pitch_up = r["up"][1]
            cfg.cal_pitch_down = r["down"][1]
        if "left" in r and "right" in r:
            cfg.cal_yaw_left = r["left"][0]
            cfg.cal_yaw_right = r["right"][0]

        yaw_ok = abs(cfg.cal_yaw_right - cfg.cal_yaw_left) >= 3.0
        pitch_ok = abs(cfg.cal_pitch_down - cfg.cal_pitch_up) >= 3.0
        cfg.calibrated = bool(yaw_ok and pitch_ok)
        try:
            cfg.save(self.save_path) if self.save_path else cfg.save()
        except Exception:
            pass
        status = "OK" if cfg.calibrated else "WEAK (moved too little; using fallback)"
        print(f"[calib] done {status}: yawL={cfg.cal_yaw_left:.1f} yawR={cfg.cal_yaw_right:.1f} "
              f"pitchUp={cfg.cal_pitch_up:.1f} pitchDown={cfg.cal_pitch_down:.1f}")
