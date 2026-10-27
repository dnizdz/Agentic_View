#!/usr/bin/env bash
# Agentic View launcher for macOS. Rescans, starts the local server, opens the browser,
# and restarts the server by itself after an in-app update (see launcher.py).
# Double-click this file in Finder (a Terminal window opens), or run:  ./start_mac.command
# First time only, if macOS blocks it: right-click the file, choose Open, then confirm. Or run:  chmod +x start_mac.command
# Extra flags are passed to the server, e.g.  ./start_mac.command --allow-bypass   or   ./start_mac.command --read-only
cd "$(dirname "$0")" || exit 1
PY="$(command -v python3 || command -v python)"
if [ -z "$PY" ]; then
  echo "Python 3.9+ not found. Install it from https://www.python.org/downloads/ (or: brew install python) and run this again." >&2
  read -r -p "Press Enter to close." _
  exit 1
fi
if ! "$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)'; then
  echo "Python 3.9 or newer is needed. Found: $("$PY" --version 2>&1)" >&2
  read -r -p "Press Enter to close." _
  exit 1
fi
"$PY" -I launcher.py "$@"
read -r -p "Server stopped. Press Enter to close." _
