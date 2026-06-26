"""Build a fully portable NaviCore bundle (zip-and-run on any 64-bit Windows 10/11).

    python build_portable.py            -> dist/NaviCore-portable/  +  dist/NaviCore-portable.zip

Uses the official Windows *embeddable* Python (the ComfyUI "python_embeded" pattern —
RESEARCH.md §8: most AV-friendly portable route, no frozen bootloader to get flagged):
  1. download python-<ver>-embed-amd64.zip and unpack to python_embeded/
  2. enable site-packages + add the app root to sys.path via the ._pth file
  3. bootstrap pip (get-pip.py) and install the runtime deps
  4. copy the app (navicore/, models/, run.bat, selftest.py, README.md)
  5. verify: run selftest.py with the embedded python  ->  must be ALL PASS
  6. zip the folder

The bundle needs no installer, no admin, no system Python. User config/calibration is
NOT copied — each machine calibrates on first run.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import urllib.request
import zipfile

PY_VER = "3.14.2"   # matches the tested dev environment
EMBED_URL = f"https://www.python.org/ftp/python/{PY_VER}/python-{PY_VER}-embed-amd64.zip"
GETPIP_URL = "https://bootstrap.pypa.io/get-pip.py"

# opencv-contrib comes in as a mediapipe dependency (don't add plain opencv-python:
# two cv2 packages in one site-packages fight each other)
RUNTIME_DEPS = ["mediapipe==0.10.35", "pywin32>=306", "pynput>=1.7",
                "pystray>=0.19", "Pillow>=10",
                # WinRT SharedReadOnly camera backend (RESEARCH.md §13.8)
                "winrt-Windows.Media.Capture", "winrt-Windows.Media.Capture.Frames",
                "winrt-Windows.Media.MediaProperties", "winrt-Windows.Graphics.Imaging",
                "winrt-Windows.Devices.Enumeration", "winrt-Windows.Storage.Streams",
                "winrt-Windows.Security.Cryptography", "winrt-Windows.Foundation",
                "winrt-Windows.Foundation.Collections", "winrt-Windows.Media.Devices"]

ROOT = os.path.dirname(os.path.abspath(__file__))
DIST = os.path.join(ROOT, "dist")
BUNDLE = os.path.join(DIST, "NaviCore-portable")
PYDIR = os.path.join(BUNDLE, "python_embeded")

APP_ITEMS = ["navicore", "models", "run.bat", "selftest.py", "requirements.txt",
             "README.md", "RESEARCH.md"]


def log(msg: str) -> None:
    print(f"[build] {msg}", flush=True)


def fetch(url: str, dest: str) -> None:
    log(f"downloading {url}")
    urllib.request.urlretrieve(url, dest)


def run(args: list[str]) -> None:
    log("> " + " ".join(os.path.basename(args[0]) if i == 0 else a
                        for i, a in enumerate(args)))
    res = subprocess.run(args, cwd=BUNDLE)
    if res.returncode != 0:
        raise SystemExit(f"[build] FAILED ({res.returncode}): {' '.join(args)}")


def main() -> int:
    global BUNDLE, PYDIR
    if os.path.exists(BUNDLE):
        log(f"removing previous {BUNDLE}")
        try:
            shutil.rmtree(BUNDLE)
        except PermissionError:
            # a NaviCore instance is probably RUNNING from that folder (model/exe
            # files locked) — build into a staging dir instead; the zip keeps the
            # canonical inner folder name either way
            BUNDLE = BUNDLE + ".staging"
            PYDIR = os.path.join(BUNDLE, "python_embeded")
            log(f"target folder is locked (NaviCore running from it?) — "
                f"building into {os.path.basename(BUNDLE)}")
            if os.path.exists(BUNDLE):
                shutil.rmtree(BUNDLE)
    os.makedirs(PYDIR, exist_ok=True)

    # 1) embeddable python
    embed_zip = os.path.join(DIST, f"python-{PY_VER}-embed-amd64.zip")
    if not os.path.exists(embed_zip):
        fetch(EMBED_URL, embed_zip)
    with zipfile.ZipFile(embed_zip) as zf:
        zf.extractall(PYDIR)
    log(f"embedded python {PY_VER} unpacked")

    # 2) ._pth: enable site (pip/site-packages) and put the app root on sys.path.
    # The ._pth fully defines sys.path in embedded mode (the script dir is NOT added),
    # so '..' (the bundle root, relative to python_embeded/) makes `import navicore`
    # work regardless of how the interpreter is invoked.
    pth = [p for p in os.listdir(PYDIR) if p.endswith("._pth")][0]
    pth_path = os.path.join(PYDIR, pth)
    with open(pth_path, "r", encoding="utf-8") as fh:
        lines = [ln.strip() for ln in fh if ln.strip()]
    out = [ln for ln in lines if not ln.startswith("#import site")]
    if ".." not in out:
        out.insert(out.index(".") + 1 if "." in out else len(out), "..")
    if "import site" not in out:
        out.append("import site")
    with open(pth_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(out) + "\n")
    log(f"patched {pth}: {out}")

    # 3) pip + deps
    pyexe = os.path.join(PYDIR, "python.exe")
    getpip = os.path.join(DIST, "get-pip.py")
    if not os.path.exists(getpip):
        fetch(GETPIP_URL, getpip)
    run([pyexe, getpip, "--no-warn-script-location"])
    run([pyexe, "-m", "pip", "install", "--no-warn-script-location", *RUNTIME_DEPS])

    # 3b) tkinter for the settings GUI — the embeddable distro ships WITHOUT it, so
    # copy it from the (full) python running this script: the tkinter package, the
    # _tkinter extension + Tcl/Tk DLLs, and the Tcl runtime library folder.
    import glob
    base = sys.base_prefix
    dlls = os.path.join(base, "DLLs")
    n = 0
    for pat in ("_tkinter*.pyd", "tcl*.dll", "tk*.dll", "zlib*.dll"):
        for f in glob.glob(os.path.join(dlls, pat)):
            shutil.copy2(f, PYDIR)
            n += 1
    shutil.copytree(os.path.join(base, "Lib", "tkinter"),
                    os.path.join(PYDIR, "tkinter"), dirs_exist_ok=True)
    shutil.copytree(os.path.join(base, "tcl"), os.path.join(PYDIR, "tcl"),
                    dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns("nmake", "*.lib", "*.sh"))
    log(f"tkinter added ({n} dlls + package + tcl runtime)")
    run([pyexe, "-c", "import tkinter; print('tkinter import OK:', tkinter.TkVersion)"])

    # 4) app files
    for item in APP_ITEMS:
        src = os.path.join(ROOT, item)
        if not os.path.exists(src):
            log(f"skip missing {item}")
            continue
        dst = os.path.join(BUNDLE, item)
        if os.path.isdir(src):
            shutil.copytree(src, dst,
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        else:
            shutil.copy2(src, dst)
    log("app files copied (user config/calibration intentionally NOT copied)")

    # 5) verify inside the bundle
    log("running selftest with the embedded python...")
    res = subprocess.run([pyexe, "selftest.py"], cwd=BUNDLE,
                         capture_output=True, text=True, timeout=300)
    tail = (res.stdout or "").strip().splitlines()[-1] if res.stdout else ""
    if res.returncode != 0 or "ALL PASS" not in tail:
        print(res.stdout[-3000:] if res.stdout else "")
        print(res.stderr[-2000:] if res.stderr else "")
        raise SystemExit("[build] selftest FAILED inside the bundle")
    log(f"selftest in bundle: {tail}")

    # 6) zip — written manually so the inner folder is always "NaviCore-portable/"
    # even when the build happened in a .staging dir
    archive = os.path.join(DIST, "NaviCore-portable.zip")
    log("zipping (this can take a minute)...")
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
        for dp, _dn, fnames in os.walk(BUNDLE):
            for fn in fnames:
                full = os.path.join(dp, fn)
                rel = os.path.relpath(full, BUNDLE)
                zf.write(full, os.path.join("NaviCore-portable", rel))
    size_mb = os.path.getsize(archive) / 1e6
    folder_mb = sum(os.path.getsize(os.path.join(dp, f))
                    for dp, _dn, fn in os.walk(BUNDLE) for f in fn) / 1e6
    log(f"DONE: {archive}")
    log(f"zip {size_mb:.0f} MB, unpacked {folder_mb:.0f} MB")
    log("on the target PC: unzip anywhere -> run.bat")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
