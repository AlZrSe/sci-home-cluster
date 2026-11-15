@echo off
REM Build the standalone worker-agent executable (run-agent / run-agent.exe)
REM and publish it into the Syncthing folder so it replicates to every node.
REM
REM Usage:
REM   scripts\build-agent.cmd            (current OS)
REM   scripts\build-agent.cmd linux
REM   scripts\build-agent.cmd windows
REM
REM Requires Python 3.11+ on PATH and the build extra (pyinstaller).

setlocal
set "ROOT=%~dp0.."
cd /d "%ROOT%"

if "%~1"=="" (
    set "TARGET="
) else (
    set "TARGET=--target %~1"
)

echo [build-agent] Installing build dependencies...
python -m pip install --quiet --upgrade pip
python -m pip install --quiet -e ".[dev,build]"
if errorlevel 1 (
    echo [build-agent] Failed to install dependencies.
    exit /b 1
)

echo [build-agent] Building agent %TARGET%...
python scripts\build_agent.py %TARGET%
if errorlevel 1 (
    echo [build-agent] Build failed.
    exit /b 1
)

echo [build-agent] Done. Binary published to syncthing\ (if not --no-publish).
endlocal