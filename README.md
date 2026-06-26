# NaviCore (prototype)

Webcam-controlled window mover for Windows. Track head pose, per-eye wink and a hand
gesture from a single webcam, then move windows between monitors into predefined zones —
CPU-only, no GPU, portable. Design rationale and the full market research are in
[RESEARCH.md](RESEARCH.md).

## The core mechanic

1. Focus the window you want to move (it becomes the OS foreground window).
2. **Wink one eye and hold ~1 s** → NaviCore grabs that foreground window.
3. Keep the eye closed and **turn your head** toward another monitor / zone. The live
   minimap shows the target zone updating.
4. **Open the eye** → the window snaps into the selected zone.

Plus: **show a hand** (Open_Palm by default) → NaviCore sends a configurable hotkey.

> Honest limitation (see RESEARCH.md §1): plain-webcam *gaze* is only ~3–4 cm accurate,
> so targeting is driven by **head pose + big zones**, not precise gaze. This is a
> deliberate, big-target interaction, not a pixel-accurate eye-mouse.

## Why head pose, not gaze, picks the monitor

Verified across the research: head turn is a large, low-noise, drift-free signal; webcam
gaze is coarse (quadrant-level) and drifts over minutes. So the monitor + zone are chosen
by where you turn your head; gaze is only an optional coarse refinement.

## Install

```
pip install -r requirements.txt
```

Models are already in `models/` (MediaPipe Face Landmarker ~3.7 MB + Gesture Recognizer
~8.4 MB, both Apache-2.0).

## Run

```
run.bat
```
or
```
python -m navicore.app
```

### Calibration (do this first)

On **first start** (no saved calibration) — and whenever you press `c` / `Ctrl+Alt+C` /
the tray's Recalibrate — NaviCore runs a **5-step guided calibration**; follow the
on-screen prompt and hold each pose ~1 s:

1. Look **straight** at your main screen.
2. Tilt head **up**, 3. tilt **down**, 4. turn **left**, 5. turn **right**.

It learns your actual yaw/pitch endpoints, so head-to-zone mapping uses *your* motion
range (and tolerates any sign convention). The result is saved (`calibrated: true`) and
**reused on every next launch** — calibration won't auto-run again unless you ask.

### Safety rules

A carry is **cancelled** (window NOT moved) when something makes the input untrustworthy:
pause, recalibration, face lost from view >0.5 s, camera stall, the winking eye held shut
past `grab_timeout_seconds`, or a display-layout change. A window is **moved only** by
deliberately re-opening the winking eye while tracking is live.

### Controls
- In the debug window: `c` recalibrate, `p` pause, `q` quit.
- Global hotkeys: `Ctrl+Alt+P` pause, `Ctrl+Alt+C` recalibrate, `Ctrl+Alt+Q` quit.
- Tray icon (bottom-right, right-click): Pause / Recalibrate / **Settings...** / Quit.

### Camera selection

NaviCore enumerates attached cameras at startup (device names via Windows PnP +
an availability probe). Pick a camera in three ways: the **tray → Camera** submenu
(radio list, e.g. `[0] Logitech BRIO / [1] Logi C270`), the **Settings window**
dropdown, or `camera_index` in the JSON. **Switching is live** — the engine closes
the old device and opens the new one within ~1 s, no restart; if the new camera
fails to open it automatically reverts.

### Sharing the camera with Zoom/Discord (no virtual camera)

Windows lets only ONE app *control* a camera at a time, and there is no way on
Windows 10 to make multiple **arbitrary** apps share one camera without a virtual
camera + driver (see RESEARCH.md §13.8 — verified against Microsoft docs). What
NaviCore *can* do, portably and with no driver, is **coexist**: it reads frames via
WinRT **SharedReadOnly** while another app controls the camera. Set `camera_backend`
(Settings → Camera → Mode, or the JSON):

- **`dshow`** (default) — exclusive control; full frames when NaviCore is alone, but
  conflicts with conferencing apps.
- **`shared`** — always SharedReadOnly: never blocks other apps, reads alongside the
  app that controls the camera. Note: if *no* app controls the camera, the stream is
  black (the sensor isn't driven) — NaviCore is idle until something opens it.
- **`auto`** (recommended for call users) — polite: NaviCore *controls* the camera
  when it's the only user, and automatically **yields to SharedReadOnly** the moment
  another app (Zoom/Discord) opens it (detected via the Windows privacy Consent
  Store), then **reclaims control** when they're done. All switches are live, ~1–3 s.

True simultaneous use by two apps *we don't control* (Zoom AND Discord at once)
requires Windows 11 24H2's "Multi-App Camera" toggle — there is no Win10 equivalent.
The simplest Win10 workaround for two apps remains a second camera (above).

### App switcher (gesture Alt-Tab)

Hold the switcher gesture (default **Victory**, configurable) for ~0.35 s — the native
Windows Alt-Tab switcher opens. **Turn your head** left/right to move the selection
like a cursor (one step per ~8°, tunable), then **release the gesture** to activate
the selected window. Face lost or 8 s timeout cancels safely (Esc — Alt can never
stick). The switcher gesture is reserved: its own tap/hold binding will not fire.
Settings: `switcher_enabled`, `switcher_gesture`, `switcher_step_deg`,
`switcher_hold_seconds`, `switcher_max_steps`, `switcher_timeout_seconds` — also
editable in the Settings window.

### Settings window

Right-click the tray icon → **Settings...** opens a per-gesture editor: for each of the
7 gestures — enable it, set how long the pose must be held, pick `tap` (fire once) or
`hold` (keys stay pressed while the pose is shown), and compose the action from
modifier checkboxes (Ctrl/Alt/Shift/Win) plus either a key (type it, or press
**Capture** and hit the combo on your keyboard) or a mouse action
(click / doubleclick / rightclick / middleclick). "Hold Ctrl+Alt and double-click" =
check Ctrl + Alt, mouse `doubleclick`. General knobs (FPS cap, wink hold, grab timeout,
gesture confidence, gaze weight) sit at the bottom. **Save applies to the running app
within ~1 second** — no restart needed (only camera/mirror changes need a restart).
The same window can be opened standalone: `python -m navicore.settings_gui`.

## Settings (`navicore_config.json`)

A fully-populated `navicore_config.json` is written next to this folder on every run —
edit it to reconfigure (it stays with the portable bundle). Key knobs:

| Setting | Meaning |
|---|---|
| `target_fps` | processing/loop FPS cap (default **30** — lower = less CPU) |
| `wink_hold_seconds` | how long to hold the wink to grab (default 0.9 s) |
| `wink_diff_margin` | asymmetry required between eyes (anti false-blink) |
| `head_yaw_span_deg` | head-turn span used only **until** you calibrate |
| `grid_cols/rows_landscape/portrait` | the snap-zone grid per orientation |
| `gesture_bindings` | per-gesture action map (see below) |
| `gaze_weight` | 0 = head pose only (default); ~0.2 blends in coarse iris gaze (experimental) |
| `grab_timeout_seconds` | eye held shut longer than this cancels the grab (no move) |

### Gesture bindings

13 bindable gestures: 7 static MediaPipe poses (`Open_Palm`, `Closed_Fist`, `Victory`,
`Thumb_Up`, `Thumb_Down`, `Pointing_Up`, `ILoveYou`) plus 6 **dynamic** gestures
detected from hand-landmark trajectories (`Swipe_Left/Right/Up/Down` — show an open
palm, then swipe; `Pinch_Out`/`Pinch_In` — spread/close thumb+index, repeat-fires
while you keep pinching, great for zoom). Dynamic defaults: swipes switch tabs
(`ctrl+tab`/`ctrl+shift+tab`) and volume (`volumeup`/`volumedown`), pinch zooms
(`ctrl+plus`/`ctrl+minus`). For dynamic gestures `hold_seconds`/`mode` are ignored.
Tuning knobs: `swipe_require_palm`, `swipe_min_travel`, `pinch_step`,
`dynamic_debounce_seconds`. Each gesture is bound independently:

```json
"Closed_Fist": { "enabled": true, "hold_seconds": 1.0, "mode": "tap",
                 "action": "ctrl+alt+doubleclick" }
```

- **`enabled`** — turn this gesture on/off.
- **`hold_seconds`** — how long you must hold the pose before it fires.
- **`mode`** — `"tap"` fires the action once; `"hold"` presses-and-holds the keys while
  the pose is shown and releases when you lower your hand.
- **`action`** — `+`-separated spec, case-insensitive:
  - modifiers: `ctrl alt shift win`
  - keys: any single char, or `enter esc tab space up down left right f1..f12 plus
    minus volumeup volumedown mute playpause nexttrack prevtrack …`
  - mouse: `click rightclick middleclick doubleclick`
  - examples: `ctrl+c` · `ctrl+alt+doubleclick` (hold Ctrl+Alt and double-click) ·
    `ctrl+shift+s` · `enter` · `rightclick`

So your example — *hold a fist 1 s → instantly Ctrl+Alt + double-click* — is the
`Closed_Fist` binding shown above.

## Layout

```
navicore/
  config.py          portable settings (+ JSON persistence)
  camera.py          threaded webcam capture (DirectShow)
  face_tracker.py    MediaPipe FaceLandmarker -> head pose + per-eye blink + coarse gaze
  wink.py            wink state machine (asymmetry + hold + bilateral reject + hysteresis)
  filters.py         One-Euro smoothing
  monitors.py        Win32 monitor enumeration + Per-Monitor-V2 DPI
  zones.py           per-monitor snap-zone grid
  target_selector.py head pose -> target monitor + zone (endpoint mapping when calibrated)
  window_mgr.py      GetForegroundWindow + SetWindowPos placement
  calibration.py     5-step guided directional calibration
  gesture.py         MediaPipe Gesture Recognizer -> per-gesture actions
  actions.py         key-combo / single-key / mouse actions, tap & hold modes
  hotkeys.py         global app-control hotkeys (pynput)
  tray.py            system-tray menu (pystray)
  engine.py          main loop + grab/carry/drop state machine + debug window
  app.py             entrypoint
models/              MediaPipe .task models
```

## Packaging to a portable zip (next step)

`PyInstaller --onedir` (avoid `--onefile`: it self-extracts to %TEMP% and trips AV).
The most AV-transparent route is the official embeddable-Python ZIP with vendored wheels
+ this folder (drop a `python_embeded\` next to `run.bat`). See RESEARCH.md §8.

## Portable build (zip for another PC)

```
python build_portable.py
```

Produces `dist/NaviCore-portable.zip` — a fully self-contained bundle (official
embeddable Python + all deps + models + the app) following the AV-friendly
"python_embeded" pattern (RESEARCH.md §8): no installer, no admin rights, no system
Python needed. On the target 64-bit Windows 10/11 machine: **unzip anywhere → run
`run.bat`**. Calibration is per-machine (the bundle ships without your personal
config), so the first launch runs the 5-step calibration. The build script verifies
itself by running `selftest.py` with the embedded Python before zipping.

## Status

Prototype. Working: tracking, per-eye wink grab/drop with safety cancels, **5-step
directional calibration** (learns range/sign, reused across launches), head-pose zone
targeting, multi-monitor placement (incl. layout hot-changes), **FPS cap**,
**per-gesture configurable actions** (combos / keys / mouse, tap & hold), **tray
Settings GUI with live key-capture and ~1 s hot-reload**, camera-stall watchdog,
**portable zip build** (`build_portable.py`). Not yet: an on-screen (Tk/GDI) zone
overlay on the target monitor, per-display gaze regression, custom-gesture training.
See RESEARCH.md §11 fallbacks.
