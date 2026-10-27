@echo off
rem Agentic View: rescan, then start the local server and open the browser.
rem Extra flags are passed to the server, e.g.  start.bat --allow-bypass   or   start.bat --read-only
cd /d "%~dp0"
where python >nul 2>nul
if errorlevel 1 (
  echo Python 3.9+ not found.
  echo Install it from https://www.python.org/downloads/ and tick "Add python.exe to PATH",
  echo or run:  winget install Python.Python.3
  echo Then close this window and run start.bat again.
  pause
  exit /b 1
)
where claude >nul 2>nul
if errorlevel 1 (
  echo Note: Claude Code is not installed. The map and board work without it. Chat needs it:
  echo open the server page and use Setup to install and sign in.
)
echo Scanning projects...
python -I scanner\scan.py
if errorlevel 1 (
  echo Scan failed. See the message above.
  pause
  exit /b 1
)
echo Starting server. Close this window or press Ctrl+C to stop.
python -I server\server.py --open %*
pause
