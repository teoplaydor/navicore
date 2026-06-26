"""Empirical camera-sharing test: can two processes read the same webcam?

Usage:
  python cam_share_test.py holder <dshow|msmf>     # opens cam, reads ~14s
  python cam_share_test.py prober <dshow|msmf>     # tries to open + read 10 frames
  python cam_share_test.py matrix                  # runs all 4 combos, prints a table
"""
from __future__ import annotations

import subprocess
import sys
import time

import cv2

BACKENDS = {"dshow": cv2.CAP_DSHOW, "msmf": cv2.CAP_MSMF}


def holder(backend: str) -> int:
    cap = cv2.VideoCapture(0, BACKENDS[backend])
    if not cap.isOpened():
        print(f"HOLDER({backend}): OPEN FAILED", flush=True)
        return 1
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
    n = 0
    t0 = time.time()
    while time.time() - t0 < 14:
        ok, _ = cap.read()
        if ok:
            n += 1
    print(f"HOLDER({backend}): read {n} frames", flush=True)
    cap.release()
    return 0


def prober(backend: str) -> int:
    t0 = time.time()
    cap = cv2.VideoCapture(0, BACKENDS[backend])
    open_t = time.time() - t0
    if not cap.isOpened():
        print(f"PROBER({backend}): open FAILED after {open_t:.1f}s", flush=True)
        return 1
    got = 0
    for _ in range(10):
        ok, f = cap.read()
        if ok and f is not None:
            got += 1
    print(f"PROBER({backend}): open OK in {open_t:.1f}s, frames {got}/10, "
          f"size {f.shape if got else '-'}", flush=True)
    cap.release()
    return 0 if got > 0 else 2


def matrix() -> int:
    print("combo                | result")
    print("---------------------|-------")
    for h in ("dshow", "msmf"):
        for p in ("dshow", "msmf"):
            hp = subprocess.Popen([sys.executable, __file__, "holder", h],
                                  stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
            time.sleep(4)   # let the holder fully own the stream
            pr = subprocess.run([sys.executable, __file__, "prober", p],
                                capture_output=True, text=True, timeout=40)
            out = (pr.stdout or "").strip() or (pr.stderr or "").strip().splitlines()[-1:]
            print(f"hold={h:5s} probe={p:5s} | {out}")
            hp.wait(timeout=20)
            print(f"   ({hp.stdout.read().strip()})")
            time.sleep(1)
    return 0


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] in ("holder", "prober"):
        raise SystemExit(globals()[sys.argv[1]](sys.argv[2]))
    raise SystemExit(matrix())
