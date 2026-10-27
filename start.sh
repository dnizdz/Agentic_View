#!/usr/bin/env bash
# Agentic View: rescan, then start the local server and open the browser.
# Extra flags are passed to the server, e.g.  ./start.sh --allow-bypass   or   ./start.sh --read-only
cd "$(dirname "$0")" || exit 1
PY="$(command -v python3 || command -v python)"
if [ -z "$PY" ]; then
  echo "Python 3.9+ not found. Install it (python.org, or your package manager) and run this again." >&2
  exit 1
fi
if ! command -v claude >/dev/null 2>&1; then
  echo "Note: Claude Code is not installed. The map and board work without it. Chat needs it:"
  echo "open the server page and use Setup to install and sign in."
fi
echo "Scanning projects..."
"$PY" -I scanner/scan.py || { echo "Scan failed. See the message above." >&2; exit 1; }
echo "Starting server. Press Ctrl+C to stop."
exec "$PY" -I server/server.py --open "$@"
