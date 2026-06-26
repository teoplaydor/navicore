"""One-Euro filter — cheap, low-latency smoothing for noisy per-frame signals.

Standard choice for gaze/head-pose (RESEARCH.md §7): trades jitter vs lag with two
knobs (min_cutoff, beta) and costs almost nothing on CPU.
Ref: Casiez et al., "1€ Filter" (CHI 2012).
"""
from __future__ import annotations

import math


class OneEuroFilter:
    def __init__(self, min_cutoff: float = 1.0, beta: float = 0.0, d_cutoff: float = 1.0):
        self.min_cutoff = float(min_cutoff)
        self.beta = float(beta)
        self.d_cutoff = float(d_cutoff)
        self._x_prev: float | None = None
        self._dx_prev: float = 0.0
        self._t_prev: float | None = None

    @staticmethod
    def _alpha(cutoff: float, dt: float) -> float:
        tau = 1.0 / (2.0 * math.pi * cutoff)
        return 1.0 / (1.0 + tau / dt)

    def __call__(self, x: float, t: float) -> float:
        if self._x_prev is None or self._t_prev is None:
            self._x_prev, self._t_prev, self._dx_prev = x, t, 0.0
            return x
        dt = t - self._t_prev
        if dt <= 0:
            dt = 1e-3
        dx = (x - self._x_prev) / dt
        a_d = self._alpha(self.d_cutoff, dt)
        dx_hat = a_d * dx + (1 - a_d) * self._dx_prev
        cutoff = self.min_cutoff + self.beta * abs(dx_hat)
        a = self._alpha(cutoff, dt)
        x_hat = a * x + (1 - a) * self._x_prev
        self._x_prev, self._dx_prev, self._t_prev = x_hat, dx_hat, t
        return x_hat

    def reset(self) -> None:
        self._x_prev = None
        self._t_prev = None
        self._dx_prev = 0.0
