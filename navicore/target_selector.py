"""Map head pose (+ optional coarse gaze) to a target monitor and zone.

Head pose is the primary, low-noise signal for "which monitor / which big zone"
(RESEARCH.md §1). Yaw selects the position across the virtual desktop left-to-right;
pitch selects the row (and disambiguates vertically-stacked monitors); gaze adds only
a small optional refinement (cfg.gaze_weight, 0 by default).

Sign convention (MediaPipe facial transformation matrix): pitch+ = head DOWN.
When calibrated, the learned endpoints absorb whatever sign/range the user produces.
"""
from __future__ import annotations

from dataclasses import dataclass

from .monitors import Monitor, virtual_desktop_bounds
from .zones import Zone, zone_at


@dataclass
class TargetSelection:
    monitor_index: int
    zone: Zone
    nx: float            # normalized x within the target monitor (0..1)
    ny: float            # normalized y within the target monitor (0..1)
    yaw_rel: float
    pitch_rel: float


def _clamp01(v: float) -> float:
    return max(0.0, min(1.0, v))


def _apply_deadzone(v: float, dz: float) -> float:
    if abs(v) <= dz:
        return 0.0
    return v - dz if v > 0 else v + dz


class TargetSelector:
    def __init__(self, monitors: list[Monitor], cfg):
        self.monitors = monitors
        self.cfg = cfg
        self._vl, self._vt, self._vr, self._vb = virtual_desktop_bounds(monitors)

    def select(self, face, gaze_weight: float = 0.0) -> TargetSelection:
        cfg = self.cfg
        yaw_rel = face.yaw - cfg.neutral_yaw_deg
        pitch_rel = face.pitch - cfg.neutral_pitch_deg

        if cfg.calibrated:
            # endpoint mapping learned by the guided calibration: handles whatever sign
            # the head-pose estimator produces and the user's real motion range.
            yl, yr = cfg.cal_yaw_left, cfg.cal_yaw_right
            f = _clamp01((face.yaw - yl) / (yr - yl)) if abs(yr - yl) > 1e-3 else 0.5
            pu, pd = cfg.cal_pitch_up, cfg.cal_pitch_down
            ny = _clamp01((face.pitch - pu) / (pd - pu)) if abs(pd - pu) > 1e-3 else 0.5
        else:
            # fallback: symmetric span around neutral with a deadzone.
            # pitch+ = head down -> larger ny -> lower row (no inversion).
            yawd = _apply_deadzone(yaw_rel, cfg.head_yaw_deadzone_deg)
            pitchd = _apply_deadzone(pitch_rel, cfg.head_pitch_deadzone_deg)
            f = _clamp01(yawd / max(1e-3, cfg.head_yaw_span_deg) * 0.5 + 0.5)
            ny = _clamp01(pitchd / max(1e-3, cfg.head_pitch_span_deg) * 0.5 + 0.5)

        if gaze_weight > 0:
            f = _clamp01((1 - gaze_weight) * f + gaze_weight * _clamp01(face.gaze_x * 0.5 + 0.5))
            ny = _clamp01((1 - gaze_weight) * ny + gaze_weight * _clamp01(face.gaze_y * 0.5 + 0.5))

        # map across the real virtual-desktop X span (handles gaps), then disambiguate
        # monitors that overlap in X (stacked layouts) by the vertical fraction.
        target_x = self._vl + f * (self._vr - self._vl)
        cands = [m for m in self.monitors if m.left <= target_x < m.right]
        if not cands:
            mon = min(self.monitors, key=lambda m: abs(m.cx - target_x))
            ny_mon = ny
        elif len(cands) == 1:
            mon = cands[0]
            ny_mon = ny
        else:
            target_y = self._vt + ny * (self._vb - self._vt)
            inside = [m for m in cands if m.top <= target_y < m.bottom]
            mon = inside[0] if inside else min(cands, key=lambda m: abs(m.cy - target_y))
            ny_mon = _clamp01((target_y - mon.top) / max(1, mon.height))
        nx = _clamp01((target_x - mon.left) / max(1, mon.width))

        zone = zone_at(mon, cfg, nx, ny_mon)
        return TargetSelection(mon.index, zone, nx, ny_mon, yaw_rel, pitch_rel)
