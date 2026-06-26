r"""Detect which apps are *currently* using the webcam, via the Windows privacy
Consent Store (RESEARCH.md §13.4/§13.8).

Undocumented but stable since ~Win10 1903: under
  HKCU\...\CapabilityAccessManager\ConsentStore\webcam[\NonPackaged]\<app>
a REG_QWORD `LastUsedTimeStop` == 0 means "in use right now". Read defensively —
it is not a public API and may differ across builds. Pure stdlib (winreg).
"""
from __future__ import annotations

import winreg

_BASE = r"Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\webcam"


def _iter_apps(root_path: str):
    try:
        root = winreg.OpenKey(winreg.HKEY_CURRENT_USER, root_path)
    except OSError:
        return
    try:
        i = 0
        while True:
            try:
                name = winreg.EnumKey(root, i)
            except OSError:
                break
            i += 1
            try:
                sub = winreg.OpenKey(root, name)
                val, _typ = winreg.QueryValueEx(sub, "LastUsedTimeStop")
                winreg.CloseKey(sub)
                yield name, int(val)
            except OSError:
                continue
    finally:
        winreg.CloseKey(root)


def webcam_users() -> list[str]:
    """App identifiers currently using the webcam (LastUsedTimeStop == 0)."""
    users: list[str] = []
    for path in (_BASE, _BASE + r"\NonPackaged"):
        for name, stop in _iter_apps(path):
            if stop == 0:
                users.append(name)
    return users


def other_app_using_camera(self_markers: list[str]) -> bool:
    """True if some app whose key does NOT match any self_marker is using the camera.
    self_markers should identify our own process (e.g. 'python', 'navicore')."""
    markers = [m.lower() for m in self_markers if m]
    for name in webcam_users():
        low = name.lower()
        if not any(m in low for m in markers):
            return True
    return False
