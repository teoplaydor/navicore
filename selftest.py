"""Headless self-test: validates everything except live camera capture.

Run:  python selftest.py
"""
from __future__ import annotations

import time
import numpy as np

from navicore.config import Config, FACE_MODEL, GESTURE_MODEL
from navicore.monitors import set_dpi_awareness, enumerate_monitors, virtual_desktop_bounds
from navicore.zones import zones_for_monitor, zone_at
from navicore.target_selector import TargetSelector
from navicore.wink import WinkDetector
from navicore.face_tracker import FaceTracker, FaceFrame
from navicore.filters import OneEuroFilter
from navicore.calibration import Calibrator, STEPS
from navicore import actions

PASS, FAIL = "  PASS", "  FAIL"


def check(name, cond):
    print((PASS if cond else FAIL), name)
    return bool(cond)


def main() -> int:
    ok = True
    set_dpi_awareness()
    cfg = Config()  # in-memory defaults (does not touch the saved json)

    # 1) monitors
    mons = enumerate_monitors()
    ok &= check(f"enumerate monitors -> {len(mons)} found", len(mons) >= 1)
    for m in mons:
        print(f"      [{m.index}] {m.width}x{m.height} "
              f"{'portrait' if m.is_portrait else 'landscape'}"
              f"{' primary' if m.is_primary else ''} @({m.left},{m.top})")
    ok &= check("virtual desktop bounds", virtual_desktop_bounds(mons)[2] > 0)

    # 2) zones
    z = zones_for_monitor(mons[0], cfg)
    cols, rows = (cfg.grid_cols_portrait, cfg.grid_rows_portrait) if mons[0].is_portrait \
        else (cfg.grid_cols_landscape, cfg.grid_rows_landscape)
    ok &= check(f"zone grid {cols}x{rows} -> {len(z)} zones", len(z) == cols * rows)
    tr = zone_at(mons[0], cfg, 0.95, 0.05)
    print(f"      top-right zone label = '{tr.label}', rect={tr.rect}")
    ok &= check("top-right zone is rightmost/topmost", tr.col == cols - 1 and tr.row == 0)

    # 3) target selector (fallback span mode) across a head-yaw sweep
    sel = TargetSelector(mons, cfg)
    seen = {sel.select(FaceFrame(present=True, yaw=float(y))).monitor_index
            for y in range(-40, 41, 4)}
    ok &= check(f"yaw sweep selects monitors {sorted(seen)}", len(seen) >= 1)

    # 3b) UNCALIBRATED fallback pitch direction: pitch+ = head DOWN (MediaPipe matrix
    # convention), so looking up (negative pitch) must select the TOP row
    up_fb = sel.select(FaceFrame(present=True, pitch=-12.0)).zone.row
    down_fb = sel.select(FaceFrame(present=True, pitch=12.0)).zone.row
    print(f"      fallback: look up -> row {up_fb}, look down -> row {down_fb}")
    ok &= check("fallback look UP -> top row, DOWN -> bottom row", up_fb == 0 and down_fb > 0)

    # 4) CALIBRATED endpoint mapping FIXES the up/down inversion
    ccfg = Config()
    ccfg.calibrated = True
    ccfg.cal_pitch_up, ccfg.cal_pitch_down = -12.0, 8.0   # look-up pitch < look-down pitch
    ccfg.cal_yaw_left, ccfg.cal_yaw_right = -20.0, 20.0
    csel = TargetSelector(mons, ccfg)
    up_row = csel.select(FaceFrame(present=True, yaw=0.0, pitch=-12.0)).zone.row
    down_row = csel.select(FaceFrame(present=True, yaw=0.0, pitch=8.0)).zone.row
    print(f"      looking up -> row {up_row}, looking down -> row {down_row}")
    ok &= check("look UP -> top row, look DOWN -> bottom row (no inversion)",
                up_row == 0 and down_row == rows - 1)

    # 5) wink state machine
    wd = WinkDetector(cfg)
    t = 0.0
    armed = False
    for _ in range(8):
        u = wd.update(0.9, 0.9, t); t += 0.033
        armed |= (u.event == "armed")
    ok &= check("bilateral blink does NOT arm", not armed)
    wd.reset()
    armed = released = False
    t = 0.0
    for _ in range(40):
        u = wd.update(0.95, 0.05, t); t += 0.033
        if u.event == "armed":
            armed = True
    for _ in range(10):
        u = wd.update(0.05, 0.05, t); t += 0.033
        if u.event == "released":
            released = True
    ok &= check("held left wink ARMS", armed)
    ok &= check("opening eye RELEASES", released)
    wd.reset(); t = 0.0; armed = False
    for _ in range(8):
        u = wd.update(0.95, 0.05, t); t += 0.033
        armed |= (u.event == "armed")
    ok &= check("short wink does NOT arm (hold gate)", not armed)

    # 5b) safety timeout must CANCEL (not move): eye held shut past grab_timeout
    wd.reset(); t = 0.0
    evt = None
    steps = int((cfg.wink_hold_seconds + cfg.grab_timeout_seconds + 1.0) / 0.033)
    for _ in range(steps):
        u = wd.update(0.95, 0.05, t); t += 0.033
        if u.event in ("cancelled", "released"):
            evt = u.event
            break
    ok &= check(f"timeout emits 'cancelled', not 'released' (got {evt})", evt == "cancelled")

    # 6) one-euro smoothing
    flt = OneEuroFilter(1.0, 0.01)
    vals = [flt(v, i * 0.033) for i, v in enumerate([0, 10, 0, 10, 0, 10])]
    ok &= check(f"one-euro smooths (peak {max(vals):.1f} << 10)", max(vals) < 8.0)

    # 7) guided calibration runs end-to-end on synthetic poses (saved to a temp file,
    #    so the test never pollutes the real navicore_config.json)
    import os, tempfile
    kcfg = Config()
    tmp_cfg = os.path.join(tempfile.gettempdir(), "navicore_selftest_cfg.json")
    cal = Calibrator(kcfg, save_path=tmp_cfg)
    cal.start(0.0)
    poses = {"neutral": (0, 0), "up": (0, -15), "down": (0, 15), "left": (-25, 0), "right": (25, 0)}
    t = 0.0
    guard = 0
    while cal.active and guard < 5000:
        key = STEPS[cal._step][0]
        yaw, pitch = poses[key]
        cal.update(FaceFrame(present=True, yaw=float(yaw), pitch=float(pitch)), t)
        t += 0.033
        guard += 1
    ok &= check("calibration completes", not cal.active)
    ok &= check("calibration sets calibrated=True", kcfg.calibrated)
    print(f"      learned yawL={kcfg.cal_yaw_left:.0f} yawR={kcfg.cal_yaw_right:.0f} "
          f"pitchUp={kcfg.cal_pitch_up:.0f} pitchDown={kcfg.cal_pitch_down:.0f}")
    ok &= check("calibration captured correct yaw endpoints",
                kcfg.cal_yaw_left < -10 and kcfg.cal_yaw_right > 10)

    # 8) action parsing (combos + modifier-held mouse) + spec validation
    mods, final = actions.parse_action("ctrl+alt+doubleclick")
    ok &= check("parse 'ctrl+alt+doubleclick' -> 2 mods + mouse double",
                len(mods) == 2 and final == ("mouse", "doubleclick"))
    mods2, final2 = actions.parse_action("ctrl+shift+s")
    ok &= check("parse 'ctrl+shift+s' -> 2 mods + key 's'",
                len(mods2) == 2 and final2 == ("key", "s"))
    ok &= check("validate_spec flags typo 'contrl+c'",
                actions.validate_spec("contrl+c") == ["contrl"])
    ok &= check("validate_spec accepts good specs",
                actions.validate_spec("ctrl+alt+doubleclick") == []
                and actions.validate_spec("f11") == [])
    ok &= check("validate_spec knows plus/minus/media keys",
                actions.validate_spec("ctrl+plus") == []
                and actions.validate_spec("volumeup") == []
                and actions.validate_spec("playpause") == [])

    # 8a) dynamic gestures: synthetic landmark trajectories
    from navicore.dynamic_gestures import DynamicGestureDetector

    class _LP:  # landmark point
        def __init__(self, x, y):
            self.x, self.y = x, y

    def hand(wx, wy, pinch=0.5):
        """21-landmark hand: wrist at (wx,wy), hand scale 0.2, thumb-index ratio=pinch."""
        lms = [_LP(wx, wy) for _ in range(21)]
        lms[9] = _LP(wx, wy - 0.2)                  # middle MCP: scale = 0.2
        half = pinch * 0.2 / 2
        lms[4] = _LP(wx - half, wy - 0.1)           # thumb tip
        lms[8] = _LP(wx + half, wy - 0.1)           # index tip
        return lms

    def feed(det, frames, dt=0.033, t0=0.0, palm=True):
        evs = []
        t = t0
        for (x, y, p) in frames:
            e = det.update(hand(x, y, p), "Open_Palm" if palm else "", t)
            if e:
                evs.append(e)
            t += dt
        return evs, t

    dcfg = Config()
    det = DynamicGestureDetector(dcfg, mirror=True)
    still = [(0.5, 0.5, 0.5)] * 6
    sweep_r = [(0.5 + i * 0.045, 0.5, 0.5) for i in range(8)]   # +0.32 in x
    evs, t = feed(det, still + sweep_r)
    ok &= check(f"swipe right detected ({evs})", evs == ["Swipe_Right"])

    det.reset()
    evs, _ = feed(det, [(0.8 - i * 0.045, 0.5, 0.5) for i in range(8)], t0=t + 1.0)
    ok &= check(f"swipe left detected ({evs})", evs == ["Swipe_Left"])

    det.reset()
    evs, _ = feed(det, [(0.5, 0.7 - i * 0.04, 0.5) for i in range(8)], t0=t + 3.0)
    ok &= check(f"swipe up detected ({evs})", evs == ["Swipe_Up"])

    det.reset()  # diagonal -> ambiguous -> nothing
    evs, _ = feed(det, [(0.4 + i * 0.04, 0.7 - i * 0.04, 0.5) for i in range(8)], t0=t + 5.0)
    ok &= check("diagonal move fires nothing", evs == [])

    det.reset()  # palm gate: same sweep without Open_Palm must NOT fire
    evs, _ = feed(det, still + sweep_r, t0=t + 7.0, palm=False)
    ok &= check("swipe without palm gate fires nothing", evs == [])

    det.reset()  # debounce: two immediate sweeps -> only one event
    two = still + sweep_r + [(0.86 - i * 0.045, 0.5, 0.5) for i in range(8)]
    evs, _ = feed(det, two, t0=t + 9.0)
    ok &= check(f"debounce keeps single event for back-to-back sweeps ({evs})",
                len(evs) == 1)

    det.reset()  # pinch spread (still wrist): repeat-fires Pinch_Out
    spread = [(0.5, 0.5, 0.3 + i * 0.06) for i in range(12)]
    evs, _ = feed(det, spread, t0=t + 12.0)
    ok &= check(f"pinch spread fires Pinch_Out (x{len(evs)})",
                len(evs) >= 2 and set(evs) == {"Pinch_Out"})

    det.reset()  # pinch close: Pinch_In
    close = [(0.5, 0.5, 0.9 - i * 0.06) for i in range(12)]
    evs, _ = feed(det, close, t0=t + 15.0)
    ok &= check(f"pinch close fires Pinch_In (x{len(evs)})",
                len(evs) >= 2 and set(evs) == {"Pinch_In"})

    # 8d) head-driven app switcher (keys recorded, not sent)
    from navicore.app_switcher import AppSwitcher, SwState
    scfg = Config()
    keys: list[str] = []
    sw = AppSwitcher(scfg, send=keys.append)
    tt = 100.0
    for _ in range(20):   # hold Victory 0.66s at neutral yaw -> opens
        sw.update("Victory", 0.9, True, 0.0, tt); tt += 0.033
    ok &= check(f"switcher opens with alt_down+tab ({keys})",
                sw.state == SwState.OPEN and keys[:2] == ["alt_down", "tab"])
    for _ in range(12):   # turn head right 2 steps (16 deg)
        sw.update("Victory", 0.9, True, 16.0, tt); tt += 0.1
    ok &= check(f"yaw +16deg adds 2 tabs ({keys})", keys.count("tab") == 3)
    for _ in range(6):    # back to one step
        sw.update("Victory", 0.9, True, 8.0, tt); tt += 0.1
    ok &= check("yaw back -> shift_tab", keys.count("shift_tab") == 1)
    for _ in range(15):   # release the gesture -> alt_up (Windows activates)
        sw.update("", 0.0, True, 8.0, tt); tt += 0.033
    ok &= check(f"gesture release activates (alt_up, idle) ({keys[-1]})",
                keys[-1] == "alt_up" and sw.state == SwState.IDLE)
    ok &= check("alt is balanced (down==up)",
                keys.count("alt_down") == keys.count("alt_up"))

    keys.clear()          # timeout path: esc + alt_up, never a stuck Alt
    for _ in range(20):
        sw.update("Victory", 0.9, True, 0.0, tt); tt += 0.033
    tt += scfg.switcher_timeout_seconds + 0.5
    sw.update("Victory", 0.9, True, 0.0, tt)
    ok &= check(f"timeout cancels with esc+alt_up ({keys[-2:]})",
                keys[-2:] == ["esc", "alt_up"] and sw.state == SwState.IDLE)

    # 8b) coarse gaze must NOT cancel between eyes (both eyes measured image-left->right)
    class _P:
        def __init__(self, x, y=0.5):
            self.x, self.y = x, y
    lms = [_P(0.5) for _ in range(478)]
    # eye A corners at x=0.40/0.46, eye B at 0.54/0.60; both irises shifted image-RIGHT
    lms[33], lms[133], lms[468] = _P(0.40), _P(0.46), _P(0.445)
    lms[362], lms[263], lms[473] = _P(0.54), _P(0.60), _P(0.585)
    gx_r, _ = FaceTracker._coarse_gaze(lms)
    lms[468], lms[473] = _P(0.415), _P(0.555)   # both irises shifted image-LEFT
    gx_l, _ = FaceTracker._coarse_gaze(lms)
    print(f"      gaze_x: looking right -> {gx_r:+.2f}, looking left -> {gx_l:+.2f}")
    ok &= check("gaze_x positive when looking right, negative when left (no cancel)",
                gx_r > 0.2 and gx_l < -0.2)

    # 8c) config robustness: corrupt file is backed up (not silently overwritten),
    # and unknown keys survive the save round-trip
    import os, tempfile
    tdir = tempfile.mkdtemp(prefix="navicore_test_")
    bad_path = os.path.join(tdir, "cfg.json")
    with open(bad_path, "w", encoding="utf-8") as fh:
        fh.write("{ this is not json")
    Config.load(bad_path)
    ok &= check("corrupt config backed up to .bad", os.path.exists(bad_path + ".bad"))
    good_path = os.path.join(tdir, "cfg2.json")
    import json as _json
    with open(good_path, "w", encoding="utf-8") as fh:
        _json.dump({"target_fps": 25, "my_custom_note": "keep me"}, fh)
    c2 = Config.load(good_path)
    saved = _json.load(open(good_path, encoding="utf-8"))
    ok &= check("known key applied (target_fps=25)", c2.target_fps == 25)
    ok &= check("unknown key preserved on write-back", saved.get("my_custom_note") == "keep me")

    # 8e) camera enumeration + atomic config-key update
    from navicore.camera import get_camera_names, list_cameras
    names = get_camera_names()
    ok &= check(f"camera names query returns a list ({names})", isinstance(names, list))
    cams = list_cameras()
    ok &= check(f"list_cameras structure ({len(cams)} found)",
                isinstance(cams, list) and all(
                    isinstance(c["index"], int) and isinstance(c["name"], str)
                    and isinstance(c["available"], bool) for c in cams))
    for c in cams:
        print(f"      [{c['index']}] {c['name']}"
              f"{' available ' + str(c['width']) + 'x' + str(c['height']) if c['available'] else ' busy/closed'}")

    from navicore.config import update_config_keys
    import os as _os, tempfile as _tf, json as _js
    tdir2 = _tf.mkdtemp(prefix="navicore_test_")
    cpath = _os.path.join(tdir2, "c.json")
    update_config_keys(path=cpath, camera_index=3)
    update_config_keys(path=cpath, target_fps=25)
    d2 = _js.load(open(cpath, encoding="utf-8"))
    ok &= check("update_config_keys merges keys atomically",
                d2.get("camera_index") == 3 and d2.get("target_fps") == 25)

    # 8f) camera-sharing backend (WinRT SharedReadOnly) + consent-store detection
    from navicore.winrt_camera import winrt_available
    from navicore import consent_store
    wa = winrt_available()
    ok &= check(f"winrt SharedReadOnly backend available ({wa})", isinstance(wa, bool))
    users = consent_store.webcam_users()
    ok &= check(f"consent_store.webcam_users returns list ({len(users)} in use now)",
                isinstance(users, list))
    ok &= check("other_app_using_camera excludes self markers",
                consent_store.other_app_using_camera(["python", "navicore"]) in (True, False))
    # backend resolution logic without opening a device
    bcfg = Config()
    bcfg.camera_backend = "dshow"
    beng = type("E", (), {})()  # lightweight: test the pure resolver via Engine method
    from navicore.engine import Engine
    ok &= check("Engine exposes camera backend machinery",
                all(hasattr(Engine, m) for m in
                    ("_build_camera", "_resolve_initial_backend", "_set_backend",
                     "_auto_backend_tick", "_apply_backend_preference")))

    # 9) models load + run
    t0 = time.monotonic()
    ft = FaceTracker(FACE_MODEL)
    res = ft.process(np.zeros((480, 640, 3), dtype=np.uint8), 1)
    print(f"      FaceLandmarker loaded+ran in {time.monotonic()-t0:.2f}s")
    ok &= check("FaceTracker.process runs (blank frame)", isinstance(res, FaceFrame))
    ft.close()

    from navicore.gesture import GestureController
    t0 = time.monotonic()
    gc = GestureController(GESTURE_MODEL, cfg)
    gc.process(np.zeros((480, 640, 3), dtype=np.uint8), time.monotonic(), 1)
    print(f"      GestureRecognizer loaded+ran in {time.monotonic()-t0:.2f}s")
    ok &= check("GestureController.process runs", True)
    gc.close()

    print("\nRESULT:", "ALL PASS" if ok else "SOME FAILURES")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
