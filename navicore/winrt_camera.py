"""WinRT MediaCapture SharedReadOnly camera backend (RESEARCH.md §13.8).

Lets NaviCore read frames from a camera that ANOTHER app (Zoom/Discord/...) is
controlling — the only portable, no-driver, no-virtual-camera way to coexist on
Win10 1607+ (MediaCaptureSharingMode.SharedReadOnly, since 10.0.14393).

VERIFIED BEHAVIOUR (live, this machine): a SharedReadOnly reader gets real pixels
ONLY while some controlling app is actively streaming the camera. With no controller
present, frames arrive but are BLACK (the sensor isn't driven). So this backend
reports `is_black` for the coexistence supervisor, which falls back to a controlling
(DirectShow) open when NaviCore is the sole user.

Same interface as camera.Camera: open()/read()/close(), .index, .mirror.
Runs its own thread with an asyncio loop; WinRT objects live entirely on that thread.
"""
from __future__ import annotations

import asyncio
import threading
import time

import cv2
import numpy as np


class WinRTCamera:
    def __init__(self, index: int = 0, width: int = 640, height: int = 480,
                 mirror: bool = True):
        self.index = index
        self.width = width
        self.height = height
        self.mirror = mirror
        self.backend = "shared"   # for logging/status parity with Camera
        self._frame = None
        self._lock = threading.Lock()
        self._running = False
        self._thread: threading.Thread | None = None
        self._last_ts = 0.0
        self._open_ok = False
        self._opened = threading.Event()
        self._err = ""
        # rolling black-frame detection (no controlling app -> black)
        self._black_run = 0
        self.is_black = True

    # ---- public interface (mirrors camera.Camera) ----
    def open(self) -> bool:
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        self._opened.wait(timeout=10.0)
        if not self._open_ok and self._err:
            print(f"[winrtcam] open failed: {self._err}")
        return self._open_ok

    def read(self):
        with self._lock:
            if self._frame is None:
                return self._last_ts, None
            return self._last_ts, self._frame.copy()

    def close(self) -> None:
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=3.0)

    # ---- worker thread ----
    def _run(self) -> None:
        try:
            asyncio.run(self._async_main())
        except Exception as exc:  # noqa: BLE001 — surface any WinRT/init failure
            self._err = f"{type(exc).__name__}: {exc}"
            self._open_ok = False
            self._opened.set()

    async def _async_main(self) -> None:
        from winrt.windows.media.capture.frames import (
            MediaFrameSourceGroup, MediaFrameSourceKind)
        from winrt.windows.media.capture import (
            MediaCapture, MediaCaptureInitializationSettings,
            MediaCaptureSharingMode, MediaCaptureMemoryPreference, StreamingCaptureMode)

        groups = await MediaFrameSourceGroup.find_all_async()
        if self.index >= groups.size:
            self._err = f"no camera at index {self.index} ({groups.size} found)"
            self._open_ok = False
            self._opened.set()
            return
        group = groups.get_at(self.index)

        settings = MediaCaptureInitializationSettings()
        settings.source_group = group
        settings.sharing_mode = MediaCaptureSharingMode.SHARED_READ_ONLY
        settings.memory_preference = MediaCaptureMemoryPreference.CPU
        settings.streaming_capture_mode = StreamingCaptureMode.VIDEO
        mc = MediaCapture()
        await mc.initialize_with_settings_async(settings)

        source = None
        for key in mc.frame_sources:
            fs = mc.frame_sources[key]
            if fs.info.source_kind == MediaFrameSourceKind.COLOR:
                source = fs
                break
        if source is None:
            self._err = "no COLOR frame source"
            self._open_ok = False
            self._opened.set()
            mc.close()
            return

        reader = await mc.create_frame_reader_async(source)
        await reader.start_async()
        self._open_ok = True
        self._opened.set()
        print(f"[winrtcam] SharedReadOnly open: [{self.index}] {group.display_name}")

        try:
            while self._running:
                arr = self._grab(reader)
                if arr is not None:
                    if self.mirror:
                        arr = cv2.flip(arr, 1)
                    with self._lock:
                        self._frame = arr
                        self._last_ts = time.monotonic()
                await asyncio.sleep(0.008)
        finally:
            try:
                await reader.stop_async()
            except Exception:
                pass
            try:
                mc.close()
            except Exception:
                pass

    def _grab(self, reader):
        from winrt.windows.graphics.imaging import (
            SoftwareBitmap, BitmapPixelFormat, BitmapAlphaMode)
        from winrt.windows.storage.streams import Buffer
        from winrt.windows.security.cryptography import CryptographicBuffer

        ref = reader.try_acquire_latest_frame()
        if ref is None:
            return None
        vmf = ref.video_media_frame
        bmp = vmf.software_bitmap if vmf is not None else None
        if bmp is None:
            return None
        if bmp.bitmap_pixel_format != BitmapPixelFormat.BGRA8:
            bmp = SoftwareBitmap.convert_with_alpha(
                bmp, BitmapPixelFormat.BGRA8, BitmapAlphaMode.IGNORE)
        w, h = bmp.pixel_width, bmp.pixel_height
        buf = Buffer(w * h * 4)
        bmp.copy_to_buffer(buf)
        data = bytes(CryptographicBuffer.copy_to_byte_array(buf))
        arr = np.frombuffer(data, np.uint8).reshape(h, w, 4)[:, :, :3]
        # cheap black-frame tracking (no controlling app -> all-black stream)
        if int(arr[::37, ::37].max()) < 6:
            self._black_run = min(self._black_run + 1, 1000)
        else:
            self._black_run = 0
        self.is_black = self._black_run >= 15
        return np.ascontiguousarray(arr)


def winrt_available() -> bool:
    try:
        import winrt.windows.media.capture  # noqa: F401
        return True
    except Exception:
        return False
