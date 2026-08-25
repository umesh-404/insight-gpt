@echo off
REM InsightGPT - one-command setup for Windows (Command Prompt).
REM
REM   setup.cmd                  set up (or repair) the full Docker stack
REM   setup.cmd --doctor         diagnose only; changes nothing
REM   setup.cmd --repair         clean rebuild + recreate, then re-verify
REM   setup.cmd --native         no Docker: prepare the local dev stack
REM   setup.cmd --skip-models    skip the Ollama model pulls
REM
REM This wrapper only finds a usable Python. All of the real work lives in
REM scripts\setup.py, so every entry point behaves identically.

setlocal enabledelayedexpansion
cd /d "%~dp0"

set "PY="
for %%C in (python python3 py) do (
    if not defined PY (
        REM The version probe also filters out the Windows Store alias stub,
        REM which exits non-zero and would otherwise open the Store.
        %%C -c "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)" >nul 2>&1
        if !errorlevel! equ 0 set "PY=%%C"
    )
)

if not defined PY (
    echo.
    echo   Python 3.9+ was not found.
    echo.
    echo   Install it from https://www.python.org/downloads/
    echo   ^(tick "Add python.exe to PATH" during install^), then run setup.cmd again.
    echo.
    exit /b 1
)

%PY% scripts\setup.py %*
exit /b %errorlevel%
