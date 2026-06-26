"""Threaded webcam capture.

Uses the DirectShow backend on Windows (CAP_DSHOW) because OpenCV's default MSMF
backend has a documented slow-open / slow-first-frame bug (RESEARCH.md §8).
A background grab thread keeps only the latest frame so the CV loop never blocks on IO.
"""
from __future__ import annotations

import json
import subprocess
import threading
import time

import cv2


def get_camera_names() -> list[str]:
    """Camera device names from Windows PnP, in enumeration order (best effort —
    DirectShow index order usually matches, but is not guaranteed by the OS)."""
    try:
        cmd = ["powershell", "-NoProfile", "-Command",
               "Get-CimInstance Win32_PnPEntity | Where-Object { $_.PNPClass -eq "
               "'Camera' -or $_.PNPClass -eq 'Image' } | "
               "Select-Object -ExpandProperty Name | ConvertTo-Json"]
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=10,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)
                             ).stdout.strip()
        if not out:
            return []
        data = json.loads(out)
        return [data] if isinstance(data, str) else [str(x) for x in data]
    except Exception:
        return []


def list_cameras(max_probe: int = 8) -> list[dict]:
    """Enumerate cameras: PnP names + an open-probe per index.

    An index that exists but fails to open is reported with available=False —
    typically it is busy (held by NaviCore itself or another app), not absent."""
    names = get_camera_names()
    upto = min(max_probe, max(len(names) + 1, 3))
    cams: list[dict] = []
    for i in range(upto):
        cap = cv2.VideoCapture(i, cv2.CAP_DSHOW if hasattr(cv2, "CAP_DSHOW") else 0)
        ok = cap.isOpened()
        w = h = 0
        if ok:
            w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        cap.release()
        named = i < len(names)
        if not ok and not named:
            continue  # nothing known at this index
        cams.append({"index": i, "name": names[i] if named else f"Camera {i}",
                     "available": bool(ok), "width": w, "height": h})
    return cams


class Camera:
    def __init__(self, index: int = 0, width: int = 640, height: int = 480, mirror: bool = True):
        self.index = index
        self.width = width
        self.height = height
        self.mirror = mirror
        self._cap: cv2.VideoCapture | None = None
        self._frame = None
        self._lock = threading.Lock()
        self._running = False
        self._thread: threading.Thread | None = None
        self._last_ts = 0.0

    def open(self) -> bool:
        backend = cv2.CAP_DSHOW if hasattr(cv2, "CAP_DSHOW") else 0
        cap = cv2.VideoCapture(self.index, backend)
        if not cap.isOpened():
            # fall back to default backend
            cap = cv2.VideoCapture(self.index)
        if not cap.isOpened():
            return False
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
        cap.set(cv2.CAP_PROP_FPS, 30)
        self._cap = cap
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        # wait briefly for the first frame
        for _ in range(50):
            if self._frame is not None:
                break
            time.sleep(0.02)
        return self._frame is not None

    def _loop(self) -> None:
        assert self._cap is not None
        while self._running:
            ok, frame = self._cap.read()
            if not ok or frame is None:
                time.sleep(0.005)
                continue
            if self.mirror:
                frame = cv2.flip(frame, 1)
            with self._lock:
                self._frame = frame
                self._last_ts = time.monotonic()

    def read(self):
        """Return (timestamp_seconds, BGR frame) or (ts, None) if no frame yet."""
        with self._lock:
            if self._frame is None:
                return self._last_ts, None
            return self._last_ts, self._frame.copy()

    def close(self) -> None:
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            if self._thread.is_alive():
                # grab thread is stuck inside a driver call (cap.read); releasing the
                # capture from under it can crash native code — let process exit clean up
                print("[camera] grab thread did not exit; skipping release")
                return
        if self._cap is not None:
            self._cap.release()
            self._cap = None
