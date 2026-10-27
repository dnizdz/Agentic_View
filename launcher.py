#!/usr/bin/env python3
"""Agentic View launcher: rescan, start the local server, and start it again when it asks for a restart
(after an update). Used by start_windows.bat, start_mac.command and start_ubuntu.sh.

Usage: python launcher.py [server flags]
With none of --read-only / --allow-edit / --allow-bypass given, it asks which access level to start with.
"""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RESTART_CODE = 75   # server.py exits with this after an update


MODE_FLAGS = ("--read-only", "--allow-edit", "--allow-bypass")
MENU = """
How much may Agentic View do on this computer?

  1  Read only     View the map, board and files. No edits, no running scripts, chat is plan-only.
  2  Allow edit    Default. Edit files, run scripts, chat may edit and run python without asking.
  3  Allow bypass  Everything in 2, plus a chat switch that lets Claude run ANY command and edit
                   any file in a project without asking. Use only on projects you trust.
"""


def choose_mode():
    """Ask once per launch which access level to start with. Returns a list of server flags."""
    if not sys.stdin.isatty():
        return ["--allow-edit"]
    print(MENU)
    while True:
        try:
            pick = input("Pick 1, 2 or 3 and press Enter [2]: ").strip() or "2"
        except EOFError:
            return ["--allow-edit"]
        if pick == "1":
            return ["--read-only"]
        if pick == "2":
            return ["--allow-edit"]
        if pick == "3":
            try:
                ok = input("Allow bypass lets Claude run any command without asking. Type YES to confirm: ").strip()
            except EOFError:
                ok = ""
            if ok == "YES":
                return ["--allow-edit", "--allow-bypass"]
            print("Not confirmed.")
            continue
        print("Please type 1, 2 or 3.")


def main():
    given = sys.argv[1:]
    # Flags on the command line win (scripted use). Otherwise ask. The choice is kept across update restarts.
    args = given if any(f in given for f in MODE_FLAGS) else choose_mode() + given
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
