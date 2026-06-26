"""Portable configuration for NaviCore.

Everything tunable lives here and is persisted to ``navicore_config.json`` next to the
project root, so the whole folder stays zip-and-run with its settings. Edit that JSON to
reconfigure (especially ``gesture_bindings``); it is created/updated on every run.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field, fields

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODELS_DIR = os.path.join(ROOT, "models")
CONFIG_PATH = os.path.join(ROOT, "navicore_config.json")

FACE_MODEL = os.path.join(MODELS_DIR, "face_landmarker.task")
GESTURE_MODEL = os.path.join(MODELS_DIR, "gesture_recognizer.task")

# The 7 bindable MediaPipe static gestures (the recognizer's 8th class is "None").
STATIC_GESTURE_NAMES = ["Closed_Fist", "Open_Palm", "Pointing_Up",
                        "Thumb_Up", "Thumb_Down", "Victory", "ILoveYou"]
# Dynamic gestures detected by our own trajectory heuristics over the 21 hand
# landmarks (RESEARCH.md §13.1): swipes (wrist velocity) and pinch (thumb-index
# distance). hold_seconds/mode are ignored for these — they always fire as taps.
DYNAMIC_GESTURE_NAMES = ["Swipe_Left", "Swipe_Right", "Swipe_Up", "Swipe_Down",
                         "Pinch_Out", "Pinch_In"]
GESTURE_NAMES = STATIC_GESTURE_NAMES + DYNAMIC_GESTURE_NAMES

# default per-gesture binding template
_BINDING_KEYS = {"enabled", "hold_seconds", "mode", "action"}


def default_gesture_bindings() -> dict:
    def b(enabled, hold, mode, action):
        # mode: "tap" = fire once after holding the pose `hold` s;
        #       "hold" = press-and-hold the keys while the pose is shown.
        return {"enabled": enabled, "hold_seconds": hold, "mode": mode, "action": action}
    return {
        # Open_Palm is OFF by default: an open palm is the natural "swipe-ready" pose
        # and gates the swipe detector — binding a tap to it too would double-fire.
        "Open_Palm":   b(False, 0.4, "tap",  "ctrl+alt+n"),
        "Closed_Fist": b(False, 1.0, "tap",  "ctrl+alt+doubleclick"),
        "Victory":     b(False, 0.5, "tap",  "ctrl+c"),
        "Thumb_Up":    b(False, 0.5, "tap",  "enter"),
        "Thumb_Down":  b(False, 0.5, "tap",  "esc"),
        "Pointing_Up": b(False, 0.5, "tap",  "up"),
        "ILoveYou":    b(False, 0.5, "tap",  "ctrl+v"),
        # dynamic (trajectory) gestures — sensible media/browser defaults
        "Swipe_Left":  b(True,  0.0, "tap",  "ctrl+shift+tab"),
        "Swipe_Right": b(True,  0.0, "tap",  "ctrl+tab"),
        "Swipe_Up":    b(True,  0.0, "tap",  "volumeup"),
        "Swipe_Down":  b(True,  0.0, "tap",  "volumedown"),
        "Pinch_Out":   b(True,  0.0, "tap",  "ctrl+plus"),
        "Pinch_In":    b(True,  0.0, "tap",  "ctrl+minus"),
    }


def update_config_keys(path: str = CONFIG_PATH, **keys) -> None:
    """Atomically set a few keys in the config file (the running engine hot-reloads
    it within ~1 s). Used by the tray camera picker and similar quick toggles."""
    data = {}
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except Exception:
            data = {}
    data.update(keys)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


@dataclass
class Config:
    # ---- camera ----
    camera_index: int = 0
    cam_width: int = 640
    cam_height: int = 480
    mirror: bool = True
    target_fps: int = 30          # processing/loop FPS cap (lower = less CPU)
    # camera backend (RESEARCH.md §13.8):
    #   "dshow"  - controlling/exclusive (full frames solo; conflicts with call apps)
    #   "shared" - WinRT SharedReadOnly (coexists with one controlling app; black if solo)
    #   "auto"   - polite: control the camera when solo, yield to SharedReadOnly when
    #              another app (Zoom/Discord) wants it, take it back when they're done
    camera_backend: str = "dshow"

    # ---- wink detection (RESEARCH.md §5) ----
    wink_closed_threshold: float = 0.50
    wink_open_threshold: float = 0.30
    wink_diff_margin: float = 0.35
    wink_hold_seconds: float = 0.9
    wink_release_seconds: float = 0.18
    blink_bilateral_reject: float = 0.45
    grab_timeout_seconds: float = 8.0

    # ---- head pose -> target (fallback span mode, used until calibrated) ----
    head_yaw_deadzone_deg: float = 6.0
    head_yaw_span_deg: float = 28.0
    head_pitch_deadzone_deg: float = 5.0
    head_pitch_span_deg: float = 18.0
    neutral_yaw_deg: float = 0.0
    neutral_pitch_deg: float = 0.0

    # ---- directional calibration (learns sign + range, fixes up/down inversion) ----
    calibrated: bool = False
    cal_yaw_left: float = 0.0      # yaw when facing the leftmost screen
    cal_yaw_right: float = 0.0     # yaw when facing the rightmost screen
    cal_pitch_up: float = 0.0      # pitch when looking up   -> top of monitor
    cal_pitch_down: float = 0.0    # pitch when looking down -> bottom of monitor
    cal_settle_seconds: float = 0.9
    cal_collect_seconds: float = 1.2

    # ---- zones (RESEARCH.md §7) ----
    grid_cols_landscape: int = 3
    grid_rows_landscape: int = 2
    grid_cols_portrait: int = 2
    grid_rows_portrait: int = 3

    # ---- smoothing ----
    oneeuro_min_cutoff: float = 1.2
    oneeuro_beta: float = 0.02

    # ---- gaze refinement (0 = head pose only; small values like 0.2 blend in the
    # coarse iris-gaze ratio — experimental, webcam gaze is region-level at best) ----
    gaze_weight: float = 0.0

    # ---- gestures -> actions (RESEARCH.md §6, §13.1) ----
    gesture_enabled: bool = True            # master on/off
    gesture_min_confidence: float = 0.55
    gesture_bindings: dict = field(default_factory=default_gesture_bindings)

    # ---- head-driven app switcher ("gesture Alt-Tab", RESEARCH/README) ----
    switcher_enabled: bool = True
    switcher_gesture: str = "Victory"     # any STATIC_GESTURE_NAMES entry
    switcher_hold_seconds: float = 0.35   # how long to show the gesture to open
    switcher_step_deg: float = 8.0        # head yaw per selection step
    switcher_max_steps: int = 8
    switcher_timeout_seconds: float = 8.0
    switcher_release_grace: float = 0.3   # classifier-flicker tolerance on release

    # ---- dynamic gestures (swipes / pinch) tuning ----
    swipe_require_palm: bool = True     # swipe counts only if Open_Palm was shown <0.8s ago
    swipe_min_travel: float = 0.22      # min wrist travel, fraction of frame width
    swipe_window_seconds: float = 0.45  # trajectory window
    pinch_step: float = 0.18            # thumb-index ratio change per fire (repeat-fires)
    dynamic_debounce_seconds: float = 0.6

    # ---- app-control hotkeys (pynput format) ----
    hotkey_pause: str = "<ctrl>+<alt>+p"
    hotkey_recalibrate: str = "<ctrl>+<alt>+c"
    hotkey_quit: str = "<ctrl>+<alt>+q"

    # ---- behaviour ----
    show_debug_window: bool = True

    def _normalize_bindings(self) -> None:
        defaults = default_gesture_bindings()
        if not isinstance(self.gesture_bindings, dict):
            self.gesture_bindings = defaults
            return
        for name in self.gesture_bindings:
            if name not in GESTURE_NAMES:
                print(f"[config] gesture_bindings has unknown gesture '{name}' "
                      f"(valid: {', '.join(GESTURE_NAMES)}) — it will never fire")
        for name, dflt in defaults.items():
            b = self.gesture_bindings.get(name)
            if not isinstance(b, dict):
                self.gesture_bindings[name] = dict(dflt)
            else:
                for k, v in dflt.items():     # backfill any missing keys
                    b.setdefault(k, v)

    def save(self, path: str = CONFIG_PATH) -> None:
        # preserve keys we don't know (typos, fields from a newer version) instead of
        # silently deleting them from the user's file
        out = {**getattr(self, "_extra", {}), **asdict(self)}
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(out, fh, indent=2, ensure_ascii=False)

    @classmethod
    def load(cls, path: str = CONFIG_PATH) -> "Config":
        cfg = cls()
        cfg._extra = {}
        corrupt = False
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                known = {f.name for f in fields(cls)}
                for k, v in data.items():
                    if k in known:
                        setattr(cfg, k, v)
                    else:
                        cfg._extra[k] = v
                        print(f"[config] unknown setting '{k}' (typo?) — kept but ignored")
            except Exception as exc:
                # do NOT silently overwrite the user's file (it may hold their
                # calibration) — back it up first, then continue with defaults
                corrupt = True
                backup = path + ".bad"
                try:
                    import shutil
                    shutil.copy2(path, backup)
                    print(f"[config] could not parse {path}: {exc}; "
                          f"backed it up to {backup} and using defaults")
                except Exception:
                    print(f"[config] could not parse {path}: {exc}; using defaults")
        cfg._normalize_bindings()
        # warn early about typo'd action specs so the user sees it at startup
        try:
            from .actions import validate_spec
            for name, b in cfg.gesture_bindings.items():
                if isinstance(b, dict) and b.get("enabled"):
                    bad = validate_spec(b.get("action", ""))
                    if bad:
                        print(f"[config] gesture '{name}' action '{b.get('action')}' "
                              f"has unknown tokens {bad} — it will be disabled")
        except Exception:
            pass
        # write back a fully-populated config so the user can see/edit all options
        try:
            cfg.save(path)
        except Exception:
            pass
        if corrupt:
            print("[config] NOTE: previous settings (incl. calibration) are in the .bad backup")
        return cfg
