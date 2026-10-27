#!/usr/bin/env python3
"""Agentic View launcher: rescan, start the local server, and start it again when it asks for a restart
(after an update). Used by start_windows.bat, start_mac.command and start_ubuntu.sh.

Usage: python launcher.py [server flags, e.g. --read-only --allow-bypass]
"""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RESTART_CODE = 75   # server.py exits with this after an update


def main():
    args = sys.argv[1:]
    first = True
    while True:
        print("Scanning projects...", flush=True)
        if subprocess.run([sys.executable, "-I", str(ROOT / "scanner" / "scan.py")], cwd=ROOT).returncode != 0:
            print("Scan failed. See the message above.", file=sys.stderr, flush=True)
            return 1
        print("Starting server. Press Ctrl+C to stop.", flush=True)
        cmd = [sys.executable, "-I", str(ROOT / "server" / "server.py")] + (["--open"] if first else []) + args
        try:
            code = subprocess.run(cmd, cwd=ROOT, env=dict(os.environ, AV_LAUNCHER="1")).returncode
        except KeyboardInterrupt:
            return 0
        if code != RESTART_CODE:
            return code
        print("Restarting after update...", flush=True)
        first = False


if __name__ == "__main__":
    sys.exit(main())
