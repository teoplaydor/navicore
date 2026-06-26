"""Monitor enumeration in the Win32 virtual-desktop coordinate space (RESEARCH.md §7/§8).

Declares Per-Monitor-V2 DPI awareness so window placement lands correctly on
mixed-DPI / secondary / portrait monitors.
"""
from __future__ import annotations

import ctypes
from dataclasses import dataclass

import win32api
import win32con


def set_dpi_awareness() -> None:
    """Per-Monitor-V2 (must run before any window is created). Win10 1703+.

    SetProcessDpiAwarenessContext returns BOOL (it does not raise on failure), so the
    return value must be checked or a failure silently leaves the process DPI-unaware.
    """
    try:
        if ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
            return
    except Exception:
        pass
    try:  # older fallback (returns HRESULT: 0 == S_OK)
        if ctypes.windll.shcore.SetProcessDpiAwareness(2) == 0:
            return
    except Exception:
        pass
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


@dataclass
class Monitor:
    index: int
    left: int
    top: int
    right: int
    bottom: int
    work_left: int
    work_top: int
    work_right: int
    work_bottom: int
    is_primary: bool

    @property
    def width(self) -> int:
        return self.right - self.left

    @property
    def height(self) -> int:
        return self.bottom - self.top

    @property
    def is_portrait(self) -> bool:
        return self.height > self.width

    @property
    def cx(self) -> float:
        return (self.left + self.right) / 2.0

    @property
    def cy(self) -> float:
        return (self.top + self.bottom) / 2.0


def enumerate_monitors() -> list[Monitor]:
    mons: list[Monitor] = []
    for i, (hmon, _hdc, _rect) in enumerate(win32api.EnumDisplayMonitors()):
        info = win32api.GetMonitorInfo(hmon)
        ml, mt, mr, mb = info["Monitor"]
        wl, wt, wr, wb = info["Work"]
        is_primary = bool(info["Flags"] & win32con.MONITORINFOF_PRIMARY)
        mons.append(Monitor(i, ml, mt, mr, mb, wl, wt, wr, wb, is_primary))
    # order left-to-right, then top-to-bottom — matches "turn head right -> next monitor"
    mons.sort(key=lambda m: (m.left, m.top))
    for i, m in enumerate(mons):
        m.index = i
    return mons


def virtual_desktop_bounds(mons: list[Monitor]) -> tuple[int, int, int, int]:
    l = min(m.left for m in mons)
    t = min(m.top for m in mons)
    r = max(m.right for m in mons)
    b = max(m.bottom for m in mons)
    return l, t, r, b
