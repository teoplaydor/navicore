"""NaviCore settings window (Tkinter, stdlib-only — keeps the bundle portable).

Launched from the tray ("Settings...") as a SEPARATE process:
    python -m navicore.settings_gui
so Tk never fights the OpenCV/engine main loop. It edits navicore_config.json;
the running engine hot-reloads the file automatically (~1 s).

Per gesture: enabled, hold time, mode (tap = fire once / hold = keep keys pressed
while the pose is shown), and the action — composed from modifier checkboxes plus
either a key (typed or live-captured) or a mouse action (click/doubleclick/...).
Example: Ctrl+Alt held + double-click = mods [ctrl, alt] + mouse 'doubleclick'.

Save only rewrites the keys this window manages and re-reads the file first, so a
calibration saved by the running engine while the window is open is never lost.
"""
from __future__ import annotations

import glob
import json
import os
import sys

# In the portable bundle Tcl/Tk lives in python_embeded/tcl/ (copied there by
# build_portable.py); point Tcl at it before the first tkinter import.
if not os.environ.get("TCL_LIBRARY"):
    for d in glob.glob(os.path.join(sys.prefix, "tcl", "tcl*")):
        if os.path.isfile(os.path.join(d, "init.tcl")):
            os.environ["TCL_LIBRARY"] = d
            break
if not os.environ.get("TK_LIBRARY"):
    for d in glob.glob(os.path.join(sys.prefix, "tcl", "tk*")):
        if os.path.isdir(d) and os.path.isfile(os.path.join(d, "tk.tcl")):
            os.environ["TK_LIBRARY"] = d
            break

import tkinter as tk
from tkinter import messagebox, ttk

from .actions import validate_spec
from .config import (CONFIG_PATH, DYNAMIC_GESTURE_NAMES,
                     STATIC_GESTURE_NAMES, Config)

_MOUSE_CHOICES = ["(none)", "click", "doubleclick", "rightclick", "middleclick"]
_MODS = ["ctrl", "alt", "shift", "win"]

# general settings shown at the bottom: (config field, label, from-str parser)
_GENERAL = [
    ("target_fps", "FPS cap", int),
    ("wink_hold_seconds", "Wink hold (s)", float),
    ("grab_timeout_seconds", "Grab timeout (s)", float),
    ("gesture_min_confidence", "Gesture confidence", float),
    ("gaze_weight", "Gaze weight (0=head only)", float),
]


def _split_spec(spec: str) -> tuple[list[str], str, str]:
    """'ctrl+alt+doubleclick' -> (['ctrl','alt'], key='', mouse='doubleclick')."""
    mods, key, mouse = [], "", "(none)"
    for raw in str(spec).split("+"):
        t = raw.strip().lower()
        if not t:
            continue
        if t in ("control",):
            t = "ctrl"
        if t in ("cmd", "super"):
            t = "win"
        if t in _MODS:
            if t not in mods:
                mods.append(t)
        elif t in ("click", "doubleclick", "double", "rightclick", "middleclick"):
            mouse = "doubleclick" if t == "double" else t
        else:
            key = t
    return mods, key, mouse


def _join_spec(mods: list[str], key: str, mouse: str) -> str:
    parts = [m for m in _MODS if m in mods]
    if mouse and mouse != "(none)":
        parts.append(mouse)
    elif key.strip():
        parts.append(key.strip().lower())
    return "+".join(parts)


class _KeyCapture:
    """Capture the next key combo pressed (modifiers + one non-modifier key)."""

    def __init__(self, widget: tk.Widget, on_done):
        self.widget = widget
        self.on_done = on_done
        self._held: set[str] = set()
        self._result: tuple[list[str], str] | None = None
        self._listener = None

    def start(self) -> bool:
        try:
            from pynput import keyboard
        except Exception as exc:
            messagebox.showerror("Capture", f"pynput unavailable: {exc}")
            return False

        modmap = {}
        for name, std in (("ctrl", "ctrl"), ("alt", "alt"), ("shift", "shift"), ("cmd", "win")):
            for suffix in ("", "_l", "_r", "_gr"):
                k = getattr(keyboard.Key, name + suffix, None)
                if k is not None:
                    modmap[k] = std

        keyname = {"page_up": "pageup", "page_down": "pagedown", "return": "enter"}

        def on_press(key):
            if key in modmap:
                self._held.add(modmap[key])
                return None
            # non-modifier key ends the capture
            if isinstance(key, keyboard.KeyCode):
                ch = key.char
                final = ch.lower() if ch and len(ch) == 1 and ch.isprintable() else ""
            else:
                n = key.name
                final = keyname.get(n, n)
            self._result = (sorted(self._held), final)
            return False  # stop listener

        self._listener = keyboard.Listener(on_press=on_press)
        self._listener.start()
        self._poll()
        return True

    def _poll(self):
        if self._result is not None:
            mods, final = self._result
            self.on_done(mods, final)
            return
        if self._listener and not self._listener.is_alive():
            self.on_done([], "")
            return
        self.widget.after(50, self._poll)

    def cancel(self):
        if self._listener:
            self._listener.stop()


class GestureRow:
    def __init__(self, parent, row, name, binding):
        self.name = name
        self.enabled = tk.BooleanVar(value=bool(binding.get("enabled")))
        self.hold = tk.StringVar(value=str(binding.get("hold_seconds", 0.5)))
        self.mode = tk.StringVar(value=binding.get("mode", "tap"))
        mods, key, mouse = _split_spec(binding.get("action", ""))
        self.modvars = {m: tk.BooleanVar(value=(m in mods)) for m in _MODS}
        self.key = tk.StringVar(value=key)
        self.mouse = tk.StringVar(value=mouse)

        ttk.Checkbutton(parent, variable=self.enabled).grid(row=row, column=0, padx=(6, 2))
        ttk.Label(parent, text=name.replace("_", " ")).grid(row=row, column=1, sticky="w", padx=2)
        ttk.Spinbox(parent, from_=0.1, to=5.0, increment=0.1, width=5,
                    textvariable=self.hold).grid(row=row, column=2, padx=2)
        ttk.Combobox(parent, values=["tap", "hold"], width=5, state="readonly",
                     textvariable=self.mode).grid(row=row, column=3, padx=2)
        for i, m in enumerate(_MODS):
            ttk.Checkbutton(parent, text=m.capitalize(), variable=self.modvars[m]
                            ).grid(row=row, column=4 + i, padx=1)
        self.key_entry = ttk.Entry(parent, textvariable=self.key, width=8)
        self.key_entry.grid(row=row, column=8, padx=2)
        self.cap_btn = ttk.Button(parent, text="Capture", width=8,
                                  command=self._capture)
        self.cap_btn.grid(row=row, column=9, padx=2)
        ttk.Combobox(parent, values=_MOUSE_CHOICES, width=11, state="readonly",
                     textvariable=self.mouse).grid(row=row, column=10, padx=2)
        self.preview = ttk.Label(parent, text="", width=24, foreground="#2a7")
        self.preview.grid(row=row, column=11, sticky="w", padx=(6, 6))

        for var in (self.key, self.mouse, *self.modvars.values()):
            var.trace_add("write", lambda *_: self._refresh())
        self._refresh()

    def _capture(self):
        self.cap_btn.config(text="press...", state="disabled")

        def done(mods, final):
            for m in _MODS:
                self.modvars[m].set(m in mods)
            if final:
                self.key.set(final)
                self.mouse.set("(none)")
            self.cap_btn.config(text="Capture", state="normal")
            self._refresh()

        if not _KeyCapture(self.cap_btn, done).start():
            self.cap_btn.config(text="Capture", state="normal")

    def spec(self) -> str:
        mods = [m for m in _MODS if self.modvars[m].get()]
        return _join_spec(mods, self.key.get(), self.mouse.get())

    def _refresh(self):
        s = self.spec()
        bad = validate_spec(s) if s else []
        self.preview.config(text=s or "(no action)",
                            foreground="#c33" if bad else "#2a7")

    def to_binding(self) -> dict:
        try:
            hold = max(0.05, float(self.hold.get().replace(",", ".")))
        except ValueError:
            hold = 0.5
        return {"enabled": bool(self.enabled.get()), "hold_seconds": hold,
                "mode": self.mode.get(), "action": self.spec()}


def main() -> int:
    cfg = Config.load()  # normalized view (also backfills missing keys)

    root = tk.Tk()
    root.title("NaviCore — Settings")
    root.resizable(False, False)

    frm = ttk.Frame(root, padding=8)
    frm.grid(sticky="nsew")

    hdr = ("", "Gesture", "Hold s", "Mode", "", "", "", "", "Key", "", "Mouse", "Action preview")
    for c, t in enumerate(hdr):
        if t:
            ttk.Label(frm, text=t, font=("Segoe UI", 9, "bold")).grid(row=0, column=c, padx=2)

    rows = []
    r = 1
    for name in STATIC_GESTURE_NAMES:
        rows.append(GestureRow(frm, r, name, cfg.gesture_bindings.get(name, {})))
        r += 1
    ttk.Label(frm, text="Dynamic (swipes / pinch — hold & mode are ignored)",
              font=("Segoe UI", 9, "bold"), foreground="#579"
              ).grid(row=r, column=0, columnspan=12, sticky="w", pady=(8, 2), padx=6)
    r += 1
    for name in DYNAMIC_GESTURE_NAMES:
        rows.append(GestureRow(frm, r, name, cfg.gesture_bindings.get(name, {})))
        r += 1

    sep = ttk.Separator(frm)
    sep.grid(row=r, column=0, columnspan=12, sticky="ew", pady=8)

    gen = ttk.Frame(frm)
    gen.grid(row=r + 1, column=0, columnspan=12, sticky="w")
    gen_vars = {}
    for i, (field, label, _parse) in enumerate(_GENERAL):
        ttk.Label(gen, text=label).grid(row=0, column=2 * i, padx=(8, 2))
        v = tk.StringVar(value=str(getattr(cfg, field)))
        ttk.Entry(gen, textvariable=v, width=6).grid(row=0, column=2 * i + 1)
        gen_vars[field] = v

    # camera picker (enumeration takes ~1-2 s; "busy" usually means the running
    # NaviCore or another app currently holds that camera — still selectable)
    cam_frame = ttk.Frame(frm)
    cam_frame.grid(row=r + 2, column=0, columnspan=12, sticky="w", pady=(6, 0))
    ttk.Label(cam_frame, text="Camera:").grid(row=0, column=0, padx=(8, 2))
    try:
        from .camera import list_cameras
        cams = list_cameras()
    except Exception:
        cams = []
    if not cams:
        cams = [{"index": int(getattr(cfg, "camera_index", 0)),
                 "name": "Default camera", "available": True}]
    cam_choices = [f"{c['index']}: {c['name']}" + ("" if c.get("available") else " (in use)")
                   for c in cams]
    cam_var = tk.StringVar()
    cur_idx = int(getattr(cfg, "camera_index", 0))
    for ch in cam_choices:
        if ch.startswith(f"{cur_idx}:"):
            cam_var.set(ch)
            break
    else:
        cam_var.set(cam_choices[0])
    ttk.Combobox(cam_frame, values=cam_choices, textvariable=cam_var, width=40,
                 state="readonly").grid(row=0, column=1, padx=2)
    ttk.Label(cam_frame, text="Mode:").grid(row=0, column=2, padx=(10, 2))
    backend_var = tk.StringVar(value=getattr(cfg, "camera_backend", "dshow"))
    ttk.Combobox(cam_frame, values=["dshow", "shared", "auto"], textvariable=backend_var,
                 width=7, state="readonly").grid(row=0, column=3, padx=2)
    ttk.Label(cam_frame,
              text="auto = share with Zoom/Discord (no virtual cam); switches live",
              foreground="#777").grid(row=1, column=1, columnspan=3, sticky="w", pady=(2, 0))

    sw = ttk.Frame(frm)
    sw.grid(row=r + 3, column=0, columnspan=12, sticky="w", pady=(6, 0))
    sw_enabled = tk.BooleanVar(value=bool(getattr(cfg, "switcher_enabled", True)))
    sw_gesture = tk.StringVar(value=getattr(cfg, "switcher_gesture", "Victory"))
    sw_step = tk.StringVar(value=str(getattr(cfg, "switcher_step_deg", 8.0)))
    ttk.Checkbutton(sw, text="App switcher: hold", variable=sw_enabled
                    ).grid(row=0, column=0, padx=(8, 2))
    ttk.Combobox(sw, values=STATIC_GESTURE_NAMES, width=12, state="readonly",
                 textvariable=sw_gesture).grid(row=0, column=1, padx=2)
    ttk.Label(sw, text="+ turn head (Alt-Tab); step deg:").grid(row=0, column=2, padx=(4, 2))
    ttk.Entry(sw, textvariable=sw_step, width=5).grid(row=0, column=3)
    ttk.Label(sw, text="release gesture = activate window", foreground="#777"
              ).grid(row=0, column=4, padx=(8, 0))

    info = ttk.Label(frm, foreground="#777", text=(
        "tap = fire once after holding the pose;  hold = keep keys pressed while the pose is shown.\n"
        "Mouse overrides Key. Example: Ctrl+Alt + doubleclick = hold Ctrl+Alt and double-click.\n"
        "Key names incl.: plus minus volumeup volumedown mute playpause nexttrack prevtrack f1..f12.\n"
        "Swipes: show an open palm, then swipe. Pinch: spread/close thumb+index (repeat-fires).\n"
        "The app-switcher gesture is reserved — its own tap/hold binding will not fire.\n"
        "The running NaviCore picks changes up automatically within ~1 s."))
    info.grid(row=r + 4, column=0, columnspan=12, sticky="w", pady=(8, 4))

    def on_save():
        bad = []
        for r in rows:
            s = r.spec()
            if r.enabled.get():
                if not s:
                    bad.append(f"{r.name}: no action set")
                else:
                    unknown = validate_spec(s)
                    if unknown:
                        bad.append(f"{r.name}: unknown tokens {unknown}")
        if bad:
            messagebox.showerror("Invalid bindings", "\n".join(bad))
            return
        # re-read the CURRENT file and update only managed keys, so a calibration the
        # engine saved while this window was open is preserved
        data = {}
        if os.path.exists(CONFIG_PATH):
            try:
                with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
            except Exception:
                data = {}
        data["gesture_bindings"] = {r.name: r.to_binding() for r in rows}
        for field, _label, parse in _GENERAL:
            try:
                data[field] = parse(gen_vars[field].get().replace(",", "."))
            except ValueError:
                pass
        data["switcher_enabled"] = bool(sw_enabled.get())
        data["switcher_gesture"] = sw_gesture.get()
        try:
            data["switcher_step_deg"] = max(2.0, float(sw_step.get().replace(",", ".")))
        except ValueError:
            pass
        try:
            data["camera_index"] = int(cam_var.get().split(":", 1)[0])
        except (ValueError, IndexError):
            pass
        if backend_var.get() in ("dshow", "shared", "auto"):
            data["camera_backend"] = backend_var.get()
        tmp = CONFIG_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, ensure_ascii=False)
        os.replace(tmp, CONFIG_PATH)   # atomic: the engine never sees a half-written file
        root.destroy()

    btns = ttk.Frame(frm)
    btns.grid(row=r + 5, column=0, columnspan=12, sticky="e", pady=(4, 0))
    ttk.Button(btns, text="Save", command=on_save).grid(row=0, column=0, padx=4)
    ttk.Button(btns, text="Cancel", command=root.destroy).grid(row=0, column=1)

    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
