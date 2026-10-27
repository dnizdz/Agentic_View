#!/usr/bin/env bash
# Agentic View: rescan, then start the local server and open the browser.
# Extra flags are passed to the server, e.g.  ./start.sh --allow-bypass   or   ./start.sh --read-only
cd "$(dirname "$0")" || exit 1
PY="$(command -v python3 || command -v python)"
if [ -z "$PY" ]; then
  echo "Python 3.9+ not found. Install it and run this again." >&2
  exit 1
fi
echo "Scanning projects..."
"$PY" -I scanner/scan.py || { echo "Scan failed. See the message above." >&2; exit 1; }
echo "Starting server. Press Ctrl+C to stop."
exec "$PY" -I server/server.py --open "$@"
