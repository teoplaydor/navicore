"""Configurable input actions: key combos, single keys, and mouse clicks, with
modifier-hold support (e.g. "ctrl+alt+doubleclick" = hold Ctrl+Alt, double-click, release).

Action spec grammar (case-insensitive, '+'-separated):
  modifiers : ctrl | control | alt | shift | win | cmd | super
  mouse     : click | leftclick | rightclick | middleclick | doubleclick | double
  keys      : single char (a, n, 1) | named (enter, esc, tab, space, up, f1..f12 ...)
Examples: "ctrl+c"  "ctrl+alt+doubleclick"  "ctrl+shift+s"  "enter"  "rightclick"
"""
from __future__ import annotations

from pynput import keyboard, mouse

_kb = keyboard.Controller()
_ms = mouse.Controller()

_MODS = {
    "ctrl": keyboard.Key.ctrl, "control": keyboard.Key.ctrl,
    "alt": keyboard.Key.alt, "shift": keyboard.Key.shift,
    "win": keyboard.Key.cmd, "cmd": keyboard.Key.cmd, "super": keyboard.Key.cmd,
}
_NAMED = {
    "enter": keyboard.Key.enter, "return": keyboard.Key.enter,
    "esc": keyboard.Key.esc, "escape": keyboard.Key.esc,
    "tab": keyboard.Key.tab, "space": keyboard.Key.space,
    "backspace": keyboard.Key.backspace, "delete": keyboard.Key.delete,
    "home": keyboard.Key.home, "end": keyboard.Key.end,
    "pageup": keyboard.Key.page_up, "pagedown": keyboard.Key.page_down,
    "up": keyboard.Key.up, "down": keyboard.Key.down,
    "left": keyboard.Key.left, "right": keyboard.Key.right,
    # '+' can't be typed literally in a '+'-separated spec — use these names
    "plus": "+", "minus": "-", "equals": "=",
    # media keys (video/music control via swipes etc.)
    "volumeup": keyboard.Key.media_volume_up,
    "volumedown": keyboard.Key.media_volume_down,
    "mute": keyboard.Key.media_volume_mute,
    "playpause": keyboard.Key.media_play_pause,
    "nexttrack": keyboard.Key.media_next,
    "prevtrack": keyboard.Key.media_previous,
    **{f"f{i}": getattr(keyboard.Key, f"f{i}") for i in range(1, 13)},
}
_MOUSE = {"click", "leftclick", "rightclick", "middleclick", "doubleclick", "double"}


def _resolve_key(token: str):
    if token in _MODS:
        return _MODS[token]
    if token in _NAMED:
        return _NAMED[token]
    if len(token) == 1:
        return token
    return None


def validate_spec(spec: str) -> list[str]:
    """Return the list of unrecognized tokens in an action spec (empty == valid).
    A typo'd modifier (e.g. 'contrl+c') must NOT silently degrade to injecting a bare
    keystroke into the focused app — callers should refuse to fire invalid specs."""
    bad = []
    for raw in str(spec).split("+"):
        t = raw.strip().lower()
        if not t:
            continue
        if t in _MODS or t in _MOUSE or t in _NAMED or len(t) == 1:
            continue
        bad.append(t)
    return bad


def parse_action(spec: str):
    """Return (mods, final) where final is ('key', k) | ('mouse', kind) | None."""
    mods, final = [], None
    for raw in str(spec).split("+"):
        t = raw.strip().lower()
        if not t:
            continue
        if t in _MODS:
            mods.append(_MODS[t])
        elif t in _MOUSE:
            final = ("mouse", t)
        else:
            k = _resolve_key(t)
            if k is not None:
                final = ("key", k)
    return mods, final


def _mouse_button(kind: str):
    if kind == "rightclick":
        return mouse.Button.right, 1
    if kind == "middleclick":
        return mouse.Button.middle, 1
    if kind in ("doubleclick", "double"):
        return mouse.Button.left, 2
    return mouse.Button.left, 1


def perform_tap(spec: str) -> None:
    """Fire the action once: hold mods, do the final key/click, release mods."""
    mods, final = parse_action(spec)
    for m in mods:
        _kb.press(m)
    try:
        if final is None:
            pass
        elif final[0] == "key":
            _kb.press(final[1])
            _kb.release(final[1])
        elif final[0] == "mouse":
            btn, count = _mouse_button(final[1])
            _ms.click(btn, count)
    finally:
        for m in reversed(mods):
            _kb.release(m)


class HeldAction:
    """A press-and-hold action (modifiers + optional key held down until release())."""

    def __init__(self, mods, key):
        self.mods = mods
        self.key = key

    def release(self) -> None:
        try:
            if self.key is not None:
                _kb.release(self.key)
        finally:
            for m in reversed(self.mods):
                _kb.release(m)


def start_hold(spec: str) -> HeldAction:
    """Press modifiers (+ a key if present) and keep them down. Mouse 'final' is ignored
    for hold mode (you can't sensibly hold a click); use tap mode for clicks."""
    mods, final = parse_action(spec)
    for m in mods:
        _kb.press(m)
    key = None
    if final and final[0] == "key":
        key = final[1]
        _kb.press(key)
    return HeldAction(mods, key)
