@echo off
REM Publish the built agent binary and assets to the Syncthing folder.
REM
REM Usage:
REM   scripts\publish-agent.cmd            (current OS)
REM   scripts\publish-agent.cmd linux
REM   scripts\publish-agent.cmd windows
REM
REM Requires the agent binary to be built first (run scripts\build-agent.cmd first).

@echo off
setlocal

set "ROOT=%~dp0.."
cd /d "%ROOT%"

if "%~1"=="" (
    set "TARGET="
) else (
    set "TARGET=--target %~1"
)

echo [publish-agent] Publishing agent to Syncthing folder %TARGET%...
python scripts\publish_agent.py %TARGET%
if errorlevel 1 (
    echo [publish-agent] Publish failed.
    exit /b 1
)

echo [publish-agent] Done. Binary and assets published to syncthing\ folder.

endlocal