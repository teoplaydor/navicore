"""System-tray menu (portable alternative to a shell context-menu — RESEARCH.md §8).

Runs in a background thread; only flips flags on the shared EngineState.
Degrades gracefully (no tray) if pystray/Pillow are unavailable.
"""
from __future__ import annotations

import threading


def _make_icon_image():
    from PIL import Image, ImageDraw
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((8, 18, 56, 46), outline=(120, 200, 255, 255), width=4)   # eye
    d.ellipse((26, 26, 38, 38), fill=(120, 200, 255, 255))               # pupil
    return img


class Tray:
    def __init__(self, state, on_recalibrate, on_quit, on_settings=None,
                 cameras=None, current_camera_index=None):
        self.state = state
        self.on_recalibrate = on_recalibrate
        self.on_quit = on_quit
        self.on_settings = on_settings
        self.cameras = cameras or []          # [{'index','name','available'}, ...]
        self.current_camera_index = current_camera_index
        self._icon = None
        self._thread: threading.Thread | None = None

    def _build(self):
        import pystray
        from pystray import MenuItem as Item, Menu

        def status_text(_item):
            with self.state.lock:
                return f"Status: {self.state.status}"

        def toggle_pause(icon, item):
            self.state.paused = not self.state.paused

        def is_paused(_item):
            return self.state.paused

        def recalibrate(icon, item):
            self.on_recalibrate()

        def quit_(icon, item):
            self.on_quit()
            icon.stop()

        def settings(icon, item):
            if self.on_settings:
                self.on_settings()

        items = [
            Item(status_text, None, enabled=False),
            Menu.SEPARATOR,
            Item("Pause", toggle_pause, checked=is_paused),
            Item("Recalibrate (5-step guided)", recalibrate),
        ]
        if self.cameras:
            def _setter(idx):
                def set_cam(icon, item):
                    from .config import update_config_keys
                    update_config_keys(camera_index=idx)   # engine hot-reloads ~1 s
                return set_cam

            def _checked(idx):
                def checked(_item):
                    cur = self.current_camera_index
                    return (cur() if callable(cur) else cur) == idx
                return checked

            cam_items = [
                Item(f"[{c['index']}] {c['name']}"
                     + ("" if c.get("available") else " (busy)"),
                     _setter(c["index"]), checked=_checked(c["index"]), radio=True)
                for c in self.cameras
            ]
            items.append(Item("Camera", Menu(*cam_items)))
        if self.on_settings:
            items.append(Item("Settings...", settings))
        items += [Menu.SEPARATOR, Item("Quit", quit_)]
        menu = Menu(*items)
        return pystray.Icon("NaviCore", _make_icon_image(), "NaviCore", menu)

    def start(self) -> bool:
        try:
            self._icon = self._build()
        except Exception as exc:
            print(f"[tray] unavailable ({exc}); continuing without tray")
            return False
        self._thread = threading.Thread(target=self._icon.run, daemon=True)
        self._thread.start()
        return True

    def stop(self) -> None:
        if self._icon is not None:
            try:
                self._icon.stop()
            except Exception:
                pass
