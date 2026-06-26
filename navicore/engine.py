"""NaviCore engine — wires tracking, wink, head-target and window moves into the
grab -> carry -> drop mechanic, with guided calibration, an FPS cap, configurable
gesture actions, and a live OpenCV debug window.

Threading: the engine loop (and all OpenCV HighGUI calls) run in the MAIN thread.
The tray and hotkey listeners run in their own threads and only flip flags on EngineState.

Safety rules (from the fresh-review pass):
  * a carry is CANCELLED (window NOT moved) on: pause, recalibrate, face lost >0.5s,
    camera stall, wink safety-timeout, monitor-layout change;
  * a window is MOVED only by the deliberate re-open of the winking eye while tracked.
"""
from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, field

import cv2
import numpy as np

from . import config as cfgmod
from . import consent_store
from .app_switcher import AppSwitcher
from .calibration import Calibrator
from .camera import Camera, list_cameras
from .winrt_camera import WinRTCamera, winrt_available
from .face_tracker import FaceTracker, FaceFrame
from .filters import OneEuroFilter
from .gesture import GestureController
from .monitors import enumerate_monitors, virtual_desktop_bounds
from .target_selector import TargetSelector, TargetSelection
from .wink import WinkDetector, Phase
from . import window_mgr
from .zones import zones_for_monitor

_FACE_LOST_GRACE = 0.5      # s of continuous face absence before a carry is cancelled
_CAMERA_STALL_AFTER = 2.0   # s without a fresh frame before camera is declared stalled
_MONITOR_CHECK_EVERY = 120  # frames between display-layout re-enumerations (~4s @30fps)


@dataclass
class EngineState:
    paused: bool = False
    running: bool = True
    request_recalibrate: bool = False
    status: str = "IDLE"
    lock: threading.Lock = field(default_factory=threading.Lock)


class Engine:
    def __init__(self, cfg):
        self.cfg = cfg
        self.state = EngineState()
        self.monitors = enumerate_monitors()
        self._mon_sig = [(m.left, m.top, m.right, m.bottom) for m in self.monitors]
        self.selector = TargetSelector(self.monitors, cfg)
        self.tracker = FaceTracker(cfgmod.FACE_MODEL, mirror=cfg.mirror)
        self.wink = WinkDetector(cfg)
        self.calibrator = Calibrator(cfg)
        # enumerate BEFORE opening our own camera (a probe can't see a busy device)
        try:
            self.camera_list = list_cameras()
        except Exception:
            self.camera_list = []
        if self.camera_list:
            print("[engine] cameras: " + "; ".join(
                f"[{c['index']}] {c['name']}{'' if c['available'] else ' (busy)'}"
                for c in self.camera_list))
        # camera backend selection (RESEARCH.md §13.8)
        self._self_markers = ["python", "navicore", "run.bat"]
        self._active_backend = self._resolve_initial_backend()
        self._auto_hi = self._auto_lo = 0   # hysteresis for auto switching
        self._last_auto_t = 0.0
        self.cam = self._build_camera(cfg.camera_index, self._active_backend)
        self.gesture: GestureController | None = None
        if cfg.gesture_enabled:
            self.gesture = GestureController(cfgmod.GESTURE_MODEL, cfg)
        self.switcher = AppSwitcher(cfg)

        self._yaw_f = OneEuroFilter(cfg.oneeuro_min_cutoff, cfg.oneeuro_beta)
        self._pitch_f = OneEuroFilter(cfg.oneeuro_min_cutoff, cfg.oneeuro_beta)
        self._gx_f = OneEuroFilter(1.0, 0.01)
        self._gy_f = OneEuroFilter(1.0, 0.01)

        # mechanic state
        self.grabbed_hwnd: int | None = None
        self.grabbed_title: str = ""
        self.last_target: TargetSelection | None = None
        self.last_move_msg: str = ""

        self._fps_hist: deque[float] = deque(maxlen=30)
        self._last_loop_t: float | None = None
        self._frame_i = 0
        self._last_cam_ts: float = -1.0
        self._cam_stalled = False
        self._face_lost_since: float | None = None
        self._cfg_mtime = self._config_mtime()

    @staticmethod
    def _config_mtime() -> float:
        try:
            import os
            return os.path.getmtime(cfgmod.CONFIG_PATH)
        except OSError:
            return 0.0

    def _check_config_reload(self) -> None:
        """Hot-reload navicore_config.json when the settings GUI (or the user) edits it."""
        if self._frame_i % 30 != 0:
            return
        m = self._config_mtime()
        if m == self._cfg_mtime:
            return
        try:
            from dataclasses import fields as dc_fields
            fresh = cfgmod.Config.load()      # re-saves a normalized file
            for f in dc_fields(cfgmod.Config):
                setattr(self.cfg, f.name, getattr(fresh, f.name))
            self.cfg._extra = getattr(fresh, "_extra", {})
            if self.gesture:
                self.gesture.revalidate()
                self.gesture.dyn.mirror = self.cfg.mirror
            self.tracker.mirror = self.cfg.mirror
            self.cam.mirror = self.cfg.mirror   # grab thread reads it live
            self._cancel_carry("settings changed")
            self._apply_backend_preference()
            self._switch_camera_if_needed()
            print("[engine] config reloaded")
        except Exception as exc:
            print(f"[engine] config reload failed: {exc}")
        self._cfg_mtime = self._config_mtime()  # after our own save

    # ---- camera backend selection (RESEARCH.md §13.8) ----
    def _build_camera(self, index: int, backend: str):
        if backend == "shared" and winrt_available():
            return WinRTCamera(index, self.cfg.cam_width, self.cfg.cam_height, self.cfg.mirror)
        return Camera(index, self.cfg.cam_width, self.cfg.cam_height, self.cfg.mirror)

    def _resolve_initial_backend(self) -> str:
        mode = getattr(self.cfg, "camera_backend", "dshow")
        if mode == "shared":
            return "shared" if winrt_available() else "dshow"
        if mode == "auto" and winrt_available():
            try:
                if consent_store.other_app_using_camera(self._self_markers):
                    return "shared"   # someone already controls the camera — ride along
            except Exception:
                pass
            return "dshow"
        return "dshow"

    def _set_backend(self, new: str, reason: str) -> None:
        if new == self._active_backend:
            return
        print(f"[engine] camera backend {self._active_backend} -> {new} ({reason})")
        try:
            self.cam.close()
        except Exception:
            pass
        idx = self.cfg.camera_index
        self._active_backend = new
        self.cam = self._build_camera(idx, new)
        if not self.cam.open():
            fallback = "dshow" if new == "shared" else "shared"
            print(f"[engine] backend {new} failed to open; falling back to {fallback}")
            self._active_backend = fallback
            self.cam = self._build_camera(idx, fallback)
            self.cam.open()
        self._last_cam_ts = -1.0
        self._cam_stalled = False
        self._auto_hi = self._auto_lo = 0
        self._cancel_carry("camera backend switch")

    def _auto_backend_tick(self, now: float) -> None:
        """Polite mode: hold the camera when solo, yield to SharedReadOnly when another
        app wants it, reclaim when they're gone. Runs ~1 Hz."""
        if self.cfg.camera_backend != "auto" or not winrt_available():
            return
        if now - self._last_auto_t < 1.0:
            return
        self._last_auto_t = now
        try:
            other = consent_store.other_app_using_camera(self._self_markers)
        except Exception:
            other = False
        if self._active_backend == "dshow":
            self._auto_hi = self._auto_hi + 1 if other else 0
            if self._auto_hi >= 2:        # ~2 s: another app wants the camera
                self._set_backend("shared", "yielding to another app")
        else:  # shared
            blackish = bool(getattr(self.cam, "is_black", False))
            self._auto_lo = self._auto_lo + 1 if (blackish and not other) else 0
            if self._auto_lo >= 3:        # ~3 s: no controller present, take it solo
                self._set_backend("dshow", "no other app — taking control")

    def _apply_backend_preference(self) -> None:
        """Honor a camera_backend change from a config hot-reload."""
        mode = getattr(self.cfg, "camera_backend", "dshow")
        if mode == "dshow" and self._active_backend != "dshow":
            self._set_backend("dshow", "config")
        elif mode == "shared" and self._active_backend != "shared" and winrt_available():
            self._set_backend("shared", "config")
        # "auto" is handled continuously by _auto_backend_tick

    def _switch_camera_if_needed(self) -> None:
        """Hot-switch to another camera when camera_index changed in the config."""
        new_idx = int(self.cfg.camera_index)
        if new_idx == self.cam.index:
            return
        old_idx = self.cam.index
        print(f"[engine] switching camera {old_idx} -> {new_idx} ...")
        try:
            self.cam.close()
        except Exception:
            pass
        self.cam = self._build_camera(new_idx, self._active_backend)
        if not self.cam.open():
            print(f"[engine] camera {new_idx} failed to open — reverting to {old_idx}")
            self.cam = self._build_camera(old_idx, self._active_backend)
            self.cam.open()
            self.cfg.camera_index = old_idx
        self._last_cam_ts = -1.0
        self._cam_stalled = False
        self.last_move_msg = f"camera: index {self.cam.index}"

    # ---- main loop ----
    def run(self) -> None:
        if not self.cam.open():
            print("[engine] ERROR: could not open camera index", self.cfg.camera_index)
            self.state.running = False
            return
        if self.cfg.calibrated:
            print("[engine] camera open. Using saved calibration "
                  "(press 'c' / Ctrl+Alt+C / tray to recalibrate).")
        else:
            print("[engine] camera open. No saved calibration — starting guided calibration...")
            self.calibrator.start(time.monotonic())

        win = "NaviCore — debug"
        if self.cfg.show_debug_window:
            cv2.namedWindow(win, cv2.WINDOW_NORMAL)
            cv2.resizeWindow(win, self.cfg.cam_width + 280, self.cfg.cam_height)

        while self.state.running:
            t0 = time.monotonic()
            # recomputed every loop so a hot-reloaded target_fps applies immediately
            frame_period = 1.0 / max(1, int(self.cfg.target_fps))
            if self.state.request_recalibrate:
                self.state.request_recalibrate = False
                self._cancel_carry("recalibrating")
                self.calibrator.start(t0)

            self._check_monitors()
            self._check_config_reload()
            self._auto_backend_tick(t0)

            ts, frame = self.cam.read()
            if frame is None:
                time.sleep(0.01)
                continue
            now = time.monotonic()
            fresh = ts != self._last_cam_ts
            if fresh:
                self._last_cam_ts = ts
                self._cam_stalled = False
            elif not self._cam_stalled and now - ts > _CAMERA_STALL_AFTER:
                # camera stopped delivering frames — stop acting on the frozen image
                self._cam_stalled = True
                self._cancel_carry("camera stalled")
                self.switcher.abort()
                if self.gesture:
                    self.gesture.release_all()
                print("[engine] camera stalled — no fresh frames; tracking suspended")
            self._frame_i += 1

            face = FaceFrame()
            cal_info = None
            wink = None
            if fresh:
                face = self.tracker.process(frame, int(now * 1000))
                if face.present:
                    face.yaw = self._yaw_f(face.yaw, now)
                    face.pitch = self._pitch_f(face.pitch, now)
                    face.gaze_x = self._gx_f(face.gaze_x, now)
                    face.gaze_y = self._gy_f(face.gaze_y, now)

                # calibration takes over the mechanic while active
                if self.calibrator.active:
                    cal_info = self.calibrator.update(face, now)
                calibrating = self.calibrator.active

                # head-driven app switcher (gesture Alt-Tab)
                if self.state.paused or calibrating:
                    self.switcher.abort()
                elif self.gesture:
                    was_open = self.switcher.active
                    self.switcher.update(self.gesture.last_gesture,
                                         self.gesture.last_score,
                                         face.present, face.yaw, now)
                    if self.switcher.active and not was_open:
                        self._cancel_carry("app switcher")   # don't carry while switching
                    self.gesture.dynamic_enabled = not self.switcher.active
                switching = self.switcher.active

                # cancel (never stale-drop) when updates can't be trusted
                if self.state.paused or calibrating or switching:
                    self._cancel_carry("paused" if self.state.paused
                                       else "app switcher" if switching else "recalibrating")
                elif not face.present:
                    if self.wink.phase != Phase.IDLE:
                        if self._face_lost_since is None:
                            self._face_lost_since = now
                        elif now - self._face_lost_since >= _FACE_LOST_GRACE:
                            self._cancel_carry("face lost")
                else:
                    self._face_lost_since = None
                    wink = self.wink.update(face.blink_left, face.blink_right, now)
                    if wink.event == "armed":
                        self._on_grab()
                    elif wink.event == "released":
                        self._on_drop()
                    elif wink.event == "cancelled":
                        self._cancel_carry("held too long")

                if face.present and not calibrating:
                    self.last_target = self.selector.select(
                        face, gaze_weight=self.cfg.gaze_weight)

                # gestures (every 2nd frame to save CPU); released while paused/calibrating
                if self.gesture:
                    if self.state.paused or calibrating:
                        self.gesture.release_all()
                    elif self._frame_i % 2 == 0:
                        try:
                            self.gesture.process(frame, now, int(now * 1000))
                        except Exception as exc:
                            print(f"[engine] gesture error: {exc}")

            self._update_status(self.calibrator.active)

            # realized FPS (period between loop iterations, after the cap)
            if self._last_loop_t is not None:
                dt = now - self._last_loop_t
                if dt > 0:
                    self._fps_hist.append(1.0 / dt)
            self._last_loop_t = now

            if self.cfg.show_debug_window:
                panel = self._render(frame, face, wink, cal_info)
                cv2.imshow(win, panel)
                key = cv2.waitKey(1) & 0xFF
                if key == ord("q"):
                    self.state.running = False
                elif key == ord("c"):
                    self.state.request_recalibrate = True
                elif key == ord("p"):
                    self.state.paused = not self.state.paused

            # ---- FPS cap ----
            loop_dt = time.monotonic() - t0
            sleep = frame_period - loop_dt
            if sleep > 0:
                time.sleep(sleep)

        self.shutdown()

    # ---- mechanic ----
    def _on_grab(self) -> None:
        src = window_mgr.get_foreground_window()
        if src is None:
            self.last_move_msg = "grab: no movable foreground window"
            self.wink.reset()
            return
        self.grabbed_hwnd, self.grabbed_title = src
        self.last_move_msg = f"GRABBED: {self.grabbed_title[:40]}"

    def _on_drop(self) -> None:
        if self.grabbed_hwnd is None or self.last_target is None:
            self.grabbed_hwnd = None
            return
        zone = self.last_target.zone
        ok = window_mgr.move_window_to_rect(self.grabbed_hwnd, zone.rect)
        self.last_move_msg = (f"{'MOVED' if ok else 'MOVE FAILED'}: "
                              f"{self.grabbed_title[:30]} -> mon{zone.monitor_index} {zone.label}")
        self.grabbed_hwnd = None
        self.grabbed_title = ""

    def _cancel_carry(self, reason: str) -> None:
        """Abort any in-progress wink/carry WITHOUT moving the window."""
        if self.grabbed_hwnd is not None:
            self.last_move_msg = f"CANCELLED ({reason}): {self.grabbed_title[:28]}"
            self.grabbed_hwnd = None
            self.grabbed_title = ""
            self.last_target = None   # never reuse a pre-cancel target for a later drop
        if self.wink.phase != Phase.IDLE:
            self.wink.reset()
        self._face_lost_since = None

    def _check_monitors(self) -> None:
        """Re-enumerate displays periodically; a layout change invalidates zone rects."""
        if self._frame_i % _MONITOR_CHECK_EVERY != 0:
            return
        try:
            mons = enumerate_monitors()
        except Exception:
            return
        sig = [(m.left, m.top, m.right, m.bottom) for m in mons]
        if sig != self._mon_sig:
            print(f"[engine] display layout changed: {len(self.monitors)} -> {len(mons)} monitors")
            self.monitors = mons
            self._mon_sig = sig
            self.selector = TargetSelector(mons, self.cfg)
            self._cancel_carry("display layout changed")
            self.last_target = None

    def _update_status(self, calibrating: bool) -> None:
        if self._cam_stalled:
            s = "CAMERA STALLED — no fresh frames"
        elif calibrating:
            s = "CALIBRATING"
        elif self.state.paused:
            s = "PAUSED"
        elif self.switcher.active:
            s = "SWITCHING APPS — turn head, release gesture to pick"
        elif self.grabbed_hwnd is not None:
            tgt = self.last_target
            where = f"mon{tgt.monitor_index} {tgt.zone.label}" if tgt else "?"
            s = f"CARRYING -> {where} (open eye to drop)"
        else:
            s = "IDLE — wink+hold to grab a window"
        with self.state.lock:
            self.state.status = s

    # ---- rendering ----
    def _render(self, frame, face: FaceFrame, wink, cal_info):
        h, w = frame.shape[:2]
        panel_w = 280
        panel = np.zeros((h, w + panel_w, 3), dtype=np.uint8)
        panel[:, :w] = frame
        x0 = w + 10

        def text(y, s, color=(230, 230, 230), scale=0.5):
            cv2.putText(panel, s, (x0, y), cv2.FONT_HERSHEY_SIMPLEX, scale, color, 1, cv2.LINE_AA)

        fps = np.mean(self._fps_hist) if self._fps_hist else 0.0
        cal_tag = "cal" if self.cfg.calibrated else "uncal"
        text(24, f"NaviCore  {fps:4.1f} fps  [{cal_tag}]  cam:{self._active_backend}",
             (120, 220, 255), 0.6)

        if not face.present:
            text(56, "no face", (80, 80, 240))
        else:
            text(54, f"yaw   {face.yaw - self.cfg.neutral_yaw_deg:+6.1f} deg")
            text(74, f"pitch {face.pitch - self.cfg.neutral_pitch_deg:+6.1f} deg (+down)")
            self._bar(panel, x0, 92, "L", face.blink_left)
            self._bar(panel, x0, 112, "R", face.blink_right)

        ph = self.wink.phase.value
        sd = self.wink.side.value if self.wink.side else "-"
        text(140, f"wink: {ph} ({sd})", (180, 255, 180))
        prog = wink.arm_progress if wink else (1.0 if ph == "armed" else 0.0)
        cv2.rectangle(panel, (x0, 148), (x0 + 200, 160), (60, 60, 60), 1)
        cv2.rectangle(panel, (x0, 148), (x0 + int(200 * prog), 160), (120, 220, 120), -1)

        if self.gesture:
            text(184, f"gesture: {self.gesture.last_gesture or '-'} {self.gesture.last_score:.2f}",
                 (200, 200, 120), 0.46)
            if self.gesture.last_fired:
                text(202, f"fired: {self.gesture.last_fired[:30]}", (160, 200, 255), 0.42)

        with self.state.lock:
            status = self.state.status
        color = (120, 220, 255) if self.grabbed_hwnd is None else (120, 255, 160)
        text(224, status[:34], color, 0.48)
        if self.last_move_msg:
            text(242, self.last_move_msg[:40], (200, 200, 200), 0.4)

        self._draw_minimap(panel, x0, 258, panel_w - 20, 112)
        text(h - 28, "keys: c=calib  p=pause  q=quit", (150, 150, 150), 0.42)

        # calibration overlay (on the camera area)
        if cal_info is not None:
            self._draw_calibration(panel, w, h, cal_info)
        return panel

    @staticmethod
    def _draw_calibration(panel, w, h, info):
        overlay = panel.copy()
        cv2.rectangle(overlay, (0, h // 2 - 60), (w, h // 2 + 60), (0, 0, 0), -1)
        cv2.addWeighted(overlay, 0.6, panel, 0.4, 0, panel)
        cv2.putText(panel, "CALIBRATION", (20, h // 2 - 32),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (120, 220, 255), 2, cv2.LINE_AA)
        cv2.putText(panel, info["prompt"][:46], (20, h // 2 - 6),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1, cv2.LINE_AA)
        cv2.putText(panel, info["sub"][:46], (20, h // 2 + 20),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (200, 200, 200), 1, cv2.LINE_AA)
        pw = w - 40
        cv2.rectangle(panel, (20, h // 2 + 36), (20 + pw, h // 2 + 48), (60, 60, 60), 1)
        cv2.rectangle(panel, (20, h // 2 + 36),
                      (20 + int(pw * info["progress"]), h // 2 + 48), (120, 220, 120), -1)

    @staticmethod
    def _bar(panel, x, y, label, val):
        cv2.putText(panel, label, (x, y + 10), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (200, 200, 200), 1)
        bx = x + 24
        cv2.rectangle(panel, (bx, y), (bx + 180, y + 12), (60, 60, 60), 1)
        col = (80, 80, 240) if val > 0.5 else (120, 200, 120)
        cv2.rectangle(panel, (bx, y), (bx + int(180 * min(1.0, val)), y + 12), col, -1)
        cv2.line(panel, (bx + 90, y), (bx + 90, y + 12), (200, 200, 80), 1)

    def _draw_minimap(self, panel, px, py, pw, ph):
        l, t, r, b = virtual_desktop_bounds(self.monitors)
        vw, vh = max(1, r - l), max(1, b - t)
        scale = min(pw / vw, ph / vh)
        ox = px + int((pw - vw * scale) / 2)
        oy = py + int((ph - vh * scale) / 2)

        def to_px(X, Y):
            return int(ox + (X - l) * scale), int(oy + (Y - t) * scale)

        tgt = self.last_target
        for m in self.monitors:
            cv2.rectangle(panel, to_px(m.left, m.top), to_px(m.right, m.bottom), (90, 90, 90), 1)
            if tgt and m.index == tgt.monitor_index:
                for z in zones_for_monitor(m, self.cfg):
                    sel = (z.col == tgt.zone.col and z.row == tgt.zone.row)
                    cv2.rectangle(panel, to_px(z.left, z.top), to_px(z.right, z.bottom),
                                  (120, 255, 160) if sel else (70, 70, 70), -1 if sel else 1)

    def shutdown(self) -> None:
        self.state.running = False
        self.switcher.abort()            # never leave a synthetic Alt held down
        if self.gesture:
            self.gesture.release_all()   # never leave hold-mode keys pressed
        try:
            self.cam.close()
        except Exception:
            pass
        try:
            self.tracker.close()
        except Exception:
            pass
        if self.gesture:
            self.gesture.close()
        if self.cfg.show_debug_window:
            try:
                cv2.destroyAllWindows()
            except Exception:
                pass
