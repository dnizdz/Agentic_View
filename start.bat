@echo off
rem Agentic View: rescan, then start the local server and open the browser.
rem Extra flags are passed to the server, e.g.  start.bat --allow-bypass   or   start.bat --read-only
cd /d "%~dp0"
where python >nul 2>nul
if errorlevel 1 (
  echo Python 3.9+ not found. Install it from https://www.python.org/downloads/ and run this again.
  pause
  exit /b 1
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
