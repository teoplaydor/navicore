"""NaviCore — webcam gaze/head/wink/gesture controlled window mover (portable, CPU-only).

See RESEARCH.md for the design rationale. Core mechanic:
  wink one eye on a window (hold ~1s) -> grab the foreground window
  turn head toward another monitor/zone -> select target
  open the eye -> the window snaps into the predefined zone.
"""

__version__ = "0.1.0"
