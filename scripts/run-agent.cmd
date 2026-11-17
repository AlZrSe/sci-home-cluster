@echo off
REM Run the worker agent from the Syncthing folder.
REM
REM Usage:
REM   scripts\run-agent.cmd
REM
REM The Syncthing folder must contain:
REM   - run-agent.exe (the agent binary, published via build-agent.cmd)
REM   - .env (node configuration with NODE_ID, SYNCTHING_ROOT, etc.)
REM
REM This script is meant to be run from the Syncthing folder on each node.
REM All parameters are configured inside this script.

@echo off
setlocal

REM ============================================================
REM CONFIGURATION - EDIT THESE VALUES FOR YOUR NODE
REM ============================================================
set "NODE_ID=node-01"
set "SYNCTHING_ROOT=%CD%"
REM ============================================================

@echo off
setlocal enabledelayedexpansion

echo [run-agent] Starting agent from Syncthing folder: %SYNCTHING_ROOT%

REM Resolve Syncthing root to absolute path
for %%I in ("%SYNCTHING_ROOT%") do set "SYNCTHING_ROOT=%%~fI"

REM Ensure we're in the Syncthing folder (where the binary lives)
if not exist "%SYNCTHING_ROOT%\run-agent.exe" (
    echo [run-agent] Error: run-agent.exe not found at "%SYNCTHING_ROOT%\run-agent.exe".
    echo [run-agent] Run scripts\build-agent.cmd first to build and publish the agent.
    exit /b 1
)

if not exist "%SYNCTHING_ROOT%\.env" (
    echo [run-agent] Warning: .env not found. Agent will prompt on first run.
)

echo [run-agent] Starting agent: node=%NODE_ID%, syncthing_root=%SYNCTHING_ROOT%
cd /d "%SYNCTHING_ROOT%"
run-agent.exe --node-id %NODE_ID% --syncthing-root "%SYNCTHING_ROOT%"

endlocal