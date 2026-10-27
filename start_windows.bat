@echo off
rem Agentic View launcher for Windows. Rescans, starts the local server, opens the browser,
rem and restarts the server by itself after an in-app update (see launcher.py).
rem Extra flags are passed to the server, e.g.  start_windows.bat --allow-bypass   or   start_windows.bat --read-only
cd /d "%~dp0"
where python >nul 2>nul
if errorlevel 1 (
  echo Python 3.9+ not found.
  echo Install it from https://www.python.org/downloads/ and tick "Add python.exe to PATH",
  echo or run:  winget install Python.Python.3
  echo Then close this window and run start_windows.bat again.
  pause
  exit /b 1
)
where claude >nul 2>nul
if errorlevel 1 (
  echo Note: Claude Code is not installed. The map and board work without it. Chat needs it:
  echo open the server page and use Setup to install and sign in.
)
python -I launcher.py %*
pause
