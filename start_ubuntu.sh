#!/usr/bin/env bash
# Agentic View launcher for Ubuntu / Debian / other Linux. Rescans, starts the local server, opens the browser,
# and restarts the server by itself after an in-app update (see launcher.py).
# Run:  ./start_ubuntu.sh        (first time: chmod +x start_ubuntu.sh)
# Extra flags are passed to the server, e.g.  ./start_ubuntu.sh --allow-bypass   or   ./start_ubuntu.sh --read-only
cd "$(dirname "$0")" || exit 1
PY="$(command -v python3 || command -v python)"
if [ -z "$PY" ]; then
  echo "Python 3.9+ not found. Install it with:  sudo apt install python3" >&2
  exit 1
fi
if ! "$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)'; then
  echo "Python 3.9 or newer is needed. Found: $("$PY" --version 2>&1)" >&2
  exit 1
fi
if ! command -v xdg-open >/dev/null 2>&1; then
  echo "xdg-open not found: the browser will not open by itself and the Open file buttons will fail."
  echo "Install it with:  sudo apt install xdg-utils   (then open the address printed below by hand)"
fi
if ! command -v claude >/dev/null 2>&1; then
  echo "Note: Claude Code is not installed. The map and board work without it. Chat needs it:"
  echo "open the server page and use Setup to install and sign in."
fi
exec "$PY" -I launcher.py "$@"
