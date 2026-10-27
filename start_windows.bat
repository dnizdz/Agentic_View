@echo off
rem Agentic View launcher for Windows. Rescans, starts the local server, opens the browser,
rem and restarts the server by itself after an in-app update (see launcher.py).
rem Extra flags are passed to the server, e.g.  start_windows.bat --allow-bypass   or   start_windows.bat --read-only
cd /d "%~dp0"
where python >nul 2>nul
if errorlevel 1 (
  echo Python 3.9+ not found. Install it from https://www.python.org/downloads/ and run this again.
  pause
  exit /b 1
)
python -I launcher.py %*
pause
