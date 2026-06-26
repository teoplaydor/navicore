@echo off
REM NaviCore portable launcher.
REM Prefers a bundled python (python_embeded\) if present, else the system python.
cd /d "%~dp0"

if exist "python_embeded\python.exe" (
    "python_embeded\python.exe" -m navicore.app
) else (
    python -m navicore.app
)

if errorlevel 1 pause
