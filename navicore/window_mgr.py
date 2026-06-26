"""Foreground-window capture and zone placement via Win32 (RESEARCH.md §8/§11).

Source window = GetForegroundWindow (more reliable than gaze-picking). Placement uses
SetWindowPos; note that SetWindowPos is a *request* — some foreign/elevated windows may
refuse or clamp the resize (same limitation as PowerToys FancyZones). We therefore
verify the result with GetWindowRect and report honest success/failure.
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes
import os

import win32con
import win32gui
import win32process

_OWN_PID = os.getpid()

_SKIP_CLASSES = {
    "Progman", "WorkerW",            # desktop
    "Shell_TrayWnd", "Shell_SecondaryTrayWnd",  # taskbar
    "Windows.UI.Core.CoreWindow",    # some shell surfaces
    "ForegroundStaging",
}

_DWMWA_EXTENDED_FRAME_BOUNDS = 9


def _pid_of(hwnd: int) -> int:
    try:
        _tid, pid = win32process.GetWindowThreadProcessId(hwnd)
        return pid
    except Exception:
        return -1


def window_title(hwnd: int) -> str:
    try:
        return win32gui.GetWindowText(hwnd) or ""
    except Exception:
        return ""


def is_movable(hwnd: int) -> bool:
    if not hwnd or not win32gui.IsWindow(hwnd) or not win32gui.IsWindowVisible(hwnd):
        return False
    if _pid_of(hwnd) == _OWN_PID:           # never grab our own overlay/preview
        return False
    try:
        cls = win32gui.GetClassName(hwnd)
    except Exception:
        cls = ""
    if cls in _SKIP_CLASSES:
        return False
    # must have a real titled top-level frame. WS_CAPTION = WS_BORDER|WS_DLGFRAME, so a
    # plain `style & WS_CAPTION` is truthy for single-bit borderless popups — require
    # BOTH bits to be set.
    style = win32gui.GetWindowLong(hwnd, win32con.GWL_STYLE)
    if (style & win32con.WS_CAPTION) != win32con.WS_CAPTION:
        return False
    return True


def get_foreground_window() -> tuple[int, str] | None:
    hwnd = win32gui.GetForegroundWindow()
    if is_movable(hwnd):
        return hwnd, window_title(hwnd)
    return None


def _invisible_border_margins(hwnd: int) -> tuple[int, int, int, int]:
    """DWM windows have invisible resize borders: GetWindowRect is larger than the
    visible frame. Returns (left, top, right, bottom) margins to compensate, so the
    VISIBLE frame lands exactly on the zone instead of leaving ~7-10px gaps."""
    try:
        rect = ctypes.wintypes.RECT()
        res = ctypes.windll.dwmapi.DwmGetWindowAttribute(
            ctypes.wintypes.HWND(hwnd), _DWMWA_EXTENDED_FRAME_BOUNDS,
            ctypes.byref(rect), ctypes.sizeof(rect))
        if res != 0:
            return 0, 0, 0, 0
        wl, wt, wr, wb = win32gui.GetWindowRect(hwnd)
        return rect.left - wl, rect.top - wt, wr - rect.right, wb - rect.bottom
    except Exception:
        return 0, 0, 0, 0


def move_window_to_rect(hwnd: int, rect: tuple[int, int, int, int]) -> bool:
    """Place hwnd into (left, top, right, bottom) in virtual-desktop coordinates.
    Returns True only if the window actually ended up (approximately) there."""
    if not hwnd or not win32gui.IsWindow(hwnd):
        return False
    left, top, right, bottom = rect
    try:
        # un-maximize / un-minimize first, otherwise SetWindowPos is ignored or the
        # window is "moved" invisibly and snaps back on restore
        placement = win32gui.GetWindowPlacement(hwnd)
        if placement[1] in (win32con.SW_SHOWMAXIMIZED, win32con.SW_SHOWMINIMIZED):
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)

        ml, mt, mr, mb = _invisible_border_margins(hwnd)
        x, y = left - ml, top - mt
        w = (right - left) + ml + mr
        h = (bottom - top) + mt + mb

        flags = win32con.SWP_NOZORDER | win32con.SWP_NOACTIVATE
        style = win32gui.GetWindowLong(hwnd, win32con.GWL_STYLE)
        if not (style & win32con.WS_THICKFRAME):
            # fixed-size window: don't corrupt its layout — keep size, center in zone
            cl, ct, cr, cb = win32gui.GetWindowRect(hwnd)
            cw, ch = cr - cl, cb - ct
            x = left + max(0, ((right - left) - cw) // 2)
            y = top + max(0, ((bottom - top) - ch) // 2)
            win32gui.SetWindowPos(hwnd, 0, x, y, 0, 0, flags | win32con.SWP_NOSIZE)
        else:
            # apply twice: the second pass settles WM_DPICHANGED self-resizing when the
            # window crosses a DPI boundary (same trick FancyZones uses)
            win32gui.SetWindowPos(hwnd, 0, x, y, w, h, flags)
            win32gui.SetWindowPos(hwnd, 0, x, y, w, h, flags)

        nl, nt, _nr, _nb = win32gui.GetWindowRect(hwnd)
        return abs(nl - x) <= 32 and abs(nt - y) <= 32
    except Exception as exc:
        print(f"[window_mgr] move failed: {exc}")
        return False
