"""NaviCore entrypoint.

  python -m navicore.app

Sets Per-Monitor-V2 DPI awareness, loads portable config, starts the tray + global
hotkeys, and runs the engine loop in the main thread.
"""
from __future__ import annotations

import sys

from .config import Config
from .monitors import set_dpi_awareness


def main() -> int:
    set_dpi_awareness()  # must be before any window/monitor query
    cfg = Config.load()

    from .engine import Engine
    engine = Engine(cfg)

    # global app-control hotkeys (portable, no admin)
    from .hotkeys import GlobalHotkeys
    hk = None
    try:
        hk = GlobalHotkeys({
            cfg.hotkey_pause: lambda: setattr(engine.state, "paused", not engine.state.paused),
            cfg.hotkey_recalibrate: lambda: setattr(engine.state, "request_recalibrate", True),
            cfg.hotkey_quit: lambda: setattr(engine.state, "running", False),
        })
        hk.start()
    except Exception as exc:
        print(f"[app] global hotkeys unavailable: {exc}")

    # system tray (background thread)
    def open_settings():
        import subprocess
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            subprocess.Popen([sys.executable, "-m", "navicore.settings_gui"],
                             creationflags=flags)
        except Exception as exc:
            print(f"[app] could not open settings: {exc}")

    from .tray import Tray
    tray = Tray(
        engine.state,
        on_recalibrate=lambda: setattr(engine.state, "request_recalibrate", True),
        on_quit=lambda: setattr(engine.state, "running", False),
        on_settings=open_settings,
        cameras=engine.camera_list,
        current_camera_index=lambda: engine.cfg.camera_index,
    )
    tray.start()

    print(__doc__)
    print("Monitors detected:")
    for m in engine.monitors:
        kind = "portrait" if m.is_portrait else "landscape"
        print(f"  [{m.index}] {m.width}x{m.height} {kind}"
              f"{' (primary)' if m.is_primary else ''} at ({m.left},{m.top})")

    try:
        engine.run()
    except KeyboardInterrupt:
        pass
    finally:
        engine.shutdown()
        if hk:
            hk.stop()
        tray.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
