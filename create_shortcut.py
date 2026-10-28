#!/usr/bin/env python3
"""Create a desktop shortcut that starts Agentic View (runs this folder's launcher for your OS).

Usage: python create_shortcut.py [--dir FOLDER] [--remove]
  --dir FOLDER  put the shortcut there instead of the Desktop
  --remove      delete the shortcut this script made

Windows: "Agentic View.lnk"        -> start_windows.bat
macOS:   "Agentic View.command"    -> start_mac.command
Linux:   "Agentic View.desktop"    -> start_ubuntu.sh (opens in a terminal)
Uses the standard library only. Move the app folder? Run this again.
"""
import argparse
import os
import stat
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
NAME = "Agentic View"


def desktop_dir():
    if os.name == "nt":
        r = subprocess.run(["powershell", "-NoProfile", "-Command", "[Environment]::GetFolderPath('Desktop')"],
                           capture_output=True, text=True)
        p = r.stdout.strip()
        if p and Path(p).is_dir():
            return Path(p)
    elif sys.platform != "darwin":
        try:
            p = subprocess.run(["xdg-user-dir", "DESKTOP"], capture_output=True, text=True).stdout.strip()
            if p and Path(p).is_dir():
                return Path(p)
        except OSError:
            pass
    return Path.home() / "Desktop"


def make_windows(dest):
    lnk = dest / (NAME + ".lnk")
    target = ROOT / "start_windows.bat"
    ps = ("$s=(New-Object -ComObject WScript.Shell).CreateShortcut($env:AV_LNK);"
          "$s.TargetPath=$env:AV_TARGET;$s.WorkingDirectory=$env:AV_DIR;"
          "$s.Description='Start Agentic View';$s.IconLocation=$env:AV_ICON;$s.Save()")
    env = dict(os.environ, AV_LNK=str(lnk), AV_TARGET=str(target), AV_DIR=str(ROOT),
               AV_ICON=str(Path(os.environ.get("SystemRoot", "C:/Windows")) / "System32" / "shell32.dll") + ",13")
    r = subprocess.run(["powershell", "-NoProfile", "-Command", ps], env=env, capture_output=True, text=True)
    if r.returncode != 0 or not lnk.exists():
        raise SystemExit("could not create the shortcut: " + (r.stderr.strip() or "unknown error"))
    return lnk


def make_mac(dest):
    f = dest / (NAME + ".command")
    f.write_text('#!/usr/bin/env bash\nexec "%s" "$@"\n' % (ROOT / "start_mac.command"), encoding="utf-8")
    f.chmod(f.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return f


def make_linux(dest):
    f = dest / (NAME + ".desktop")
    f.write_text("[Desktop Entry]\nType=Application\nName=%s\nComment=Start Agentic View\nExec=\"%s\"\nPath=%s\n"
                 "Terminal=true\nCategories=Development;\n" % (NAME, ROOT / "start_ubuntu.sh", ROOT), encoding="utf-8")
    f.chmod(f.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    (ROOT / "start_ubuntu.sh").chmod((ROOT / "start_ubuntu.sh").stat().st_mode | stat.S_IXUSR)
    try:   # Ubuntu GNOME shows "Allow Launching" until the file is marked trusted
        subprocess.run(["gio", "set", str(f), "metadata::trusted", "true"], capture_output=True)
    except OSError:
        pass
    return f


def main():
    ap = argparse.ArgumentParser(description="Create a desktop shortcut for Agentic View")
    ap.add_argument("--dir", help="folder for the shortcut (default: your Desktop)")
    ap.add_argument("--remove", action="store_true", help="delete the shortcut")
    a = ap.parse_args()
    dest = Path(a.dir).expanduser().resolve() if a.dir else desktop_dir()
    if os.name == "nt":
        make, ext = make_windows, ".lnk"
    elif sys.platform == "darwin":
        make, ext = make_mac, ".command"
    else:
        make, ext = make_linux, ".desktop"
    if a.remove:
        f = dest / (NAME + ext)
        if f.exists():
            f.unlink()
            print("removed " + str(f), flush=True)
        else:
            print("no shortcut at " + str(f), flush=True)
        return
    if not dest.is_dir():
        raise SystemExit("folder not found: " + str(dest))
    print("created " + str(make(dest)), flush=True)


if __name__ == "__main__":
    main()
