#!/usr/bin/env python3
"""Agentic_View local server. Standard library only.

Serves the viewer and a small API so the page can: stream a Claude chat per
project, run or stop scanned scripts, read and edit project files, rescan, and
set up Claude (install + login).

This is a browser-to-shell bridge. Guards:
  - binds 127.0.0.1 only; Host and Origin headers are checked (blocks DNS rebinding)
  - random token per start, required on every API call (header) and on /out (cookie)
  - the page sends project ids and file ids; the server maps them to folders
    from the scanned graph. No raw paths, commands or args come from the page.
  - file access is confined to the project folder and refuses secret files
  - the install command is fixed per OS and must be echoed back by the page
  - started by you, stopped by you. Never a service.

Usage:
  python server/server.py [--port 8765] [--open] [--read-only] [--allow-bypass]
"""
import argparse
import base64
import codecs
import fnmatch
import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "out"
VIEWER = ROOT / "viewer"
try:
    VERSION = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
except OSError:
    VERSION = ""
sys.path.insert(0, str(ROOT / "scanner"))
import scan as scanmod  # noqa: E402  (read-only reuse of defaults)

LOCAL_CFG = ROOT / "scanner" / "config.local.json"
CFG = scanmod.load_config(str(LOCAL_CFG) if LOCAL_CFG.exists() else None)
SECRET_GLOBS = CFG["exclude_file_globs"] + [".env*", "*.secret", "id_rsa*", "*.pfx"]
SKIP_DIRS = set(CFG["exclude_dir_names"]) | {".git"}
MAX_BODY = 2_000_000
MAX_FILE = 1_000_000
MAX_RUN_BYTES = 5_000_000
OUT_FILES = {"graph.js", "graph.json", "impact.json", "INDEX.md"}
SID_RE = re.compile(r"^[0-9a-fA-F-]{36}$")
MODEL_RE = re.compile(r"^[A-Za-z0-9._\[\]-]{1,60}$")

# --allow-edit: on top of the Accept edits mode, chat may run python without asking
# (the rule form differs per shell tool, so both are listed).
ALLOW_EDIT_TOOLS = ["Bash(python *)", "PowerShell(python *)"]

S = {"token": "", "port": 0, "read_only": False, "allow_bypass": False, "allow_edit": False,
     "runs": {}, "graph": None, "graph_mtime": 0}
S_LOCK = threading.Lock()


def log(msg):
    print(msg, flush=True)


# ------------------------------------------------------------------ graph
def graph():
    p = OUT / "graph.json"
    try:
        mt = p.stat().st_mtime
    except OSError:
        return {"projects": {}, "files": {}, "meta": {}}
    if S["graph"] is None or mt != S["graph_mtime"]:
        g = json.loads(p.read_text(encoding="utf-8"))
        projects = {n["id"]: n for n in g["nodes"] if n["type"] == "project"}
        files = {n["id"]: n for n in g["nodes"] if n["type"] == "file"}
        S["graph"] = {"projects": projects, "files": files, "meta": g["meta"]}
        S["graph_mtime"] = mt
    return S["graph"]


def project_dir(pid):
    n = graph()["projects"].get(pid)
    if not n:
        return None
    return Path(n["path"])


def is_secret(name):
    return any(fnmatch.fnmatch(name.lower(), g.lower()) for g in SECRET_GLOBS)


def confine(pid, rel):
    """Resolve rel inside the project folder. Returns Path or None."""
    base = project_dir(pid)
    if base is None or not rel or "\x00" in rel:
        return None
    parts = Path(rel.replace("\\", "/")).parts
    if any(p in SKIP_DIRS or p in ("..", ".") for p in parts) or is_secret(parts[-1]):
        return None
    real_base = os.path.realpath(base)
    real = os.path.realpath(os.path.join(real_base, *parts))
    if not (real == real_base or real.startswith(real_base + os.sep)):
        return None
    return Path(real)



SCAN_ROOT = Path(CFG["root"]).resolve()
GLOBAL_MD = Path.home() / ".claude" / "CLAUDE.md"   # the only file the global editor can touch
NAME_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9 ._&()+-]{0,58}[A-Za-z0-9)])?$")
RESERVED = {"con", "prn", "aux", "nul"} | {f"com{i}" for i in range(1, 10)} | {f"lpt{i}" for i in range(1, 10)}


def clean_line(x, n):
    return re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", "", str(x)).replace("\r", "").strip()[:n]


def new_project(b):
    """Create a project folder with Goal, Tasks, Related projects, Handover and architecture stub."""
    raw = str(b.get("name", ""))
    name = raw.strip()
    if raw != name:
        return (400, "name must not start or end with a space")
    if not NAME_RE.match(name) or name.lower() in RESERVED or name.lower() in {d.lower() for d in SKIP_DIRS}:
        return (400, "bad name: use letters, digits, spaces and . _ & ( ) + - (1-60 chars)")
    target = SCAN_ROOT / name
    if os.path.exists(target) or name in graph()["projects"]:
        return (409, "a project with this name already exists")
    if os.path.realpath(target) != os.path.join(os.path.realpath(SCAN_ROOT), name):
        return (400, "bad name")
    goal = clean_line(b.get("goal", ""), 1500) or "TBD"
    tasks = [clean_line(t, 300) for t in (b.get("tasks") or [])][:30]
    tasks = [t for t in tasks if t]
    known = graph()["projects"]
    related = []
    for r in (b.get("related") or [])[:25]:
        r = str(r)
        if r not in known or known[r].get("archived"):
            return (400, f"unknown related project: {clean_line(r, 60)}")
        related.append(r)
    today = time.strftime("%Y-%m-%d")
    impact = str(ROOT / "scanner" / "impact.py")
    rel_md = "\n".join(f"- `{r}` - (what this project uses from it)" for r in related) or \
        "None yet. Add one bullet per project, with the project name in backticks."
    task_md = "\n".join(f"- [ ] {t}" for t in tasks) or "- [ ] (none yet)"
    claude_md = f"""# {name}

## Goal
{goal}

## Tasks
{task_md}

## Related projects
Projects this one uses or depends on. Read their CLAUDE.md and architecture.md before building anything that touches them. The Agentic View scanner reads this list to draw the dependency.
{rel_md}

## Rules
- Before changing shared code, check what depends on it: `python "{impact}" <file>`.
- Keep Handover/Handover.md current: each pending item needs what, why, exact paths, files to read first, and the order of blocking items.
- Keep architecture.md current when a module boundary or an external call changes.
- Working material goes in Brief/YYYY-MM-DD Topic/. Final artifacts go in Deliverables/ with a date-prefixed name.
"""
    items = "\n".join(
        f"### Task {i}: {t}\n- What: {t}\n- Why: serves the Goal in CLAUDE.md\n- Read first: CLAUDE.md" +
        (", plus CLAUDE.md and architecture.md of each related project" if related else "") +
        "\n- Files: not started (re-verify paths before editing; line numbers drift)\n- Blocked by: nothing recorded\n"
        for i, t in enumerate(tasks, 1)) or "No tasks yet.\n"
    handover = f"# Handover - {name}\n\nCreated {today}. Read CLAUDE.md first (goal, related projects).\n\n## Pending\n{items}"
    arch = f"""# Architecture

Update this file when a module boundary or an external call changes.

## Overview
{goal}. No code yet.

## Entry points
None yet.

## Modules
None yet.

## Dependency graph (condensed)
None yet.

## External system boundaries
None currently.{(' Planned related projects: ' + ', '.join(related) + '.') if related else ''}

## Known gaps / TODO
- Not built yet. Regenerate with the architecture-doc skill once code exists.

## Last checked
- {today}: created with the project, no code to check.
"""
    try:
        for d in ("Brief", "Deliverables", "Handover"):
            (target / d).mkdir(parents=True, exist_ok=False)
        for rel, txt in (("CLAUDE.md", claude_md), ("Handover/Handover.md", handover), ("architecture.md", arch)):
            with open(target / rel, "x", encoding="utf-8", newline="\n") as f:
                f.write(txt)
    except OSError as e:
        import shutil as _sh
        if target.exists():
            _sh.rmtree(target, ignore_errors=True)
        return (500, f"could not create project: {type(e).__name__}")
    log(f"created project {name} ({len(tasks)} tasks, {len(related)} related)")
    return {"id": name}


# ------------------------------------------------------------------ claude
_shell_probe = [0.0, None]


def claude_bin():
    found = shutil.which("claude")
    if found:
        return found
    home = Path.home()
    if os.name == "nt":
        cands = [home / ".local" / "bin" / "claude.exe"]
    else:
        cands = [home / ".local" / "bin" / "claude", home / ".claude" / "local" / "claude",
                 home / ".npm-global" / "bin" / "claude", home / ".bun" / "bin" / "claude",
                 Path("/opt/homebrew/bin/claude"), Path("/usr/local/bin/claude")]
    for cand in cands:
        if cand.is_file() and os.access(cand, os.X_OK):
            return str(cand)
    if os.name != "nt" and time.time() - _shell_probe[0] > 60:
        # PATH can miss nvm or custom npm prefixes when launched from Finder: ask the login shell (at most once a minute)
        sh = os.environ.get("SHELL") or "/bin/zsh"
        out = run_capture([sh, "-l", "-i", "-c", "command -v claude"], timeout=8)
        line = out.splitlines()[-1].strip() if out else ""
        _shell_probe[0] = time.time()
        _shell_probe[1] = line if line.startswith("/") and os.access(line, os.X_OK) else None
    return _shell_probe[1] if os.name != "nt" else None


def run_capture(argv, timeout=15):
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=timeout,
                           encoding="utf-8", errors="replace",
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return (r.stdout or "").strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def install_spec():
    if os.name == "nt":
        cmd = "irm https://claude.ai/install.ps1 | iex"
        return cmd, ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", cmd]
    cmd = "curl -fsSL https://claude.ai/install.sh | bash"
    return cmd, ["bash", "-c", cmd]


def project_settings(pid):
    base = project_dir(pid)
    out = {"defaultMode": None, "allow": 0, "deny": 0, "hooks": False, "files": []}
    if not base:
        return out
    for name in ("settings.json", "settings.local.json"):
        p = base / ".claude" / name
        if not p.exists():
            continue
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        out["files"].append(name)
        perm = d.get("permissions", {}) or {}
        out["defaultMode"] = perm.get("defaultMode") or out["defaultMode"]
        out["allow"] += len(perm.get("allow", []) or [])
        out["deny"] += len(perm.get("deny", []) or [])
        out["hooks"] = out["hooks"] or bool(d.get("hooks"))
    return out


# ------------------------------------------------------------------ runs
class Run:
    def __init__(self, kind, label, argv, cwd, stdin_text=None, keep_stdin=False, env=None):
        self.id = uuid.uuid4().hex[:12]
        self.kind, self.label, self.argv, self.cwd = kind, label, argv, str(cwd)
        self.chunks, self.size, self.done, self.code = [], 0, False, None
        self.cond = threading.Condition()
        self.started = time.time()
        self.project = None
        self.meta = {}
        self.pending, self._lbuf = {}, ""   # session only: open can_use_tool requests by request_id
        e = dict(os.environ)
        e.update({"PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8"})
        e.update(env or {})
        kw = {}
        if os.name == "nt":
            kw["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
        else:
            kw["start_new_session"] = True
        self.proc = subprocess.Popen(
            argv, cwd=str(cwd), env=e, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            stdin=subprocess.PIPE if (stdin_text is not None or keep_stdin) else subprocess.DEVNULL, **kw)
        if stdin_text is not None:
            try:
                self.proc.stdin.write(stdin_text.encode("utf-8"))
                self.proc.stdin.close()
            except OSError:
                pass
        threading.Thread(target=self._read, daemon=True).start()

    def _scan(self, text):
        """Track tool-permission requests the CLI sends on stdout, so /permit can answer them."""
        self._lbuf += text
        *lines, self._lbuf = self._lbuf.split("\n")
        self._lbuf = self._lbuf[-200000:]
        for ln in lines:
            if '"control_re' not in ln:
                continue
            try:
                o = json.loads(ln)
            except ValueError:
                continue
            if o.get("type") == "control_request" and (o.get("request") or {}).get("subtype") == "can_use_tool":
                self.pending[o.get("request_id")] = o["request"]
            elif o.get("type") == "control_cancel_request":
                self.pending.pop(o.get("request_id"), None)

    def permit(self, rid, decision):
        """Answer one pending permission request. decision: allow | allow_session | deny. Returns None or (code, msg)."""
        req = self.pending.pop(rid, None)
        if req is None or self.done:
            return (404, "no such pending request")
        if decision == "deny":
            resp = {"behavior": "deny", "message": "Denied by the user in Agentic View"}
        else:
            resp = {"behavior": "allow", "updatedInput": req.get("input", {})}
            sug = [dict(x, destination="session") for x in req.get("permission_suggestions") or []
                   if isinstance(x, dict) and x.get("type") == "addRules"]
            if decision == "allow_session" and sug:
                resp["updatedPermissions"] = sug
        self.send(json.dumps({"type": "control_response", "response": {"subtype": "success", "request_id": rid, "response": resp}}) + "\n")
        self._push(json.dumps({"type": "av_permit", "request_id": rid, "decision": decision}) + "\n")
        return None

    def _push(self, text):
        if self.kind == "session":
            self._scan(text)
        with self.cond:
            if self.size < MAX_RUN_BYTES:
                self.chunks.append(text)
                self.size += len(text)
                if self.size >= MAX_RUN_BYTES:
                    self.chunks.append("\n[output limit reached, further output dropped]\n")
            self.cond.notify_all()

    def _read(self):
        dec = codecs.getincrementaldecoder("utf-8")("replace")
        fd = self.proc.stdout.fileno()
        while True:
            try:
                data = os.read(fd, 4096)
            except OSError:
                break
            if not data:
                break
            self._push(dec.decode(data))
        tail = dec.decode(b"", final=True)
        if tail:
            self._push(tail)
        self.code = self.proc.wait()
        with self.cond:
            self.done = True
            self.pending.clear()
            self.cond.notify_all()
        log(f"run {self.id} ({self.kind}: {self.label}) exited {self.code}")

    def send(self, text):
        if self.proc.stdin and not self.done:
            self.proc.stdin.write(text.encode("utf-8"))
            self.proc.stdin.flush()

    def stop(self):
        if self.done:
            return
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(self.proc.pid), "/T", "/F"], capture_output=True,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        else:
            import signal
            try:
                os.killpg(os.getpgid(self.proc.pid), signal.SIGTERM)
            except OSError:
                pass

    def info(self):
        return {"id": self.id, "kind": self.kind, "label": self.label, "done": self.done,
                "code": self.code, "started": int(self.started), "project": self.project, "meta": self.meta}


def start_run(*a, **kw):
    r = Run(*a, **kw)
    with S_LOCK:
        S["runs"][r.id] = r
    log(f"run {r.id} ({r.kind}: {r.label}) started")
    return r


# ------------------------------------------------------------------ open, upload, jobs
# Opening a file with its default app would RUN these on Windows or macOS, so they are refused.
RUNNABLE_EXT = {".bat", ".cmd", ".com", ".exe", ".msi", ".lnk", ".scr", ".hta", ".reg", ".vbs", ".vbe", ".js", ".jse",
                ".wsf", ".wsh", ".ps1", ".psm1", ".py", ".pyw", ".sh", ".command", ".app", ".jar", ".url", ".cpl", ".msc"}
MAX_UPLOAD = 25_000_000
UPLOAD_BAD = re.compile(r'[\x00-\x1f<>:"/\\|?*]')


def open_path(fp, mode):
    """Reveal fp in the file manager, or open it with its default app. Returns None or (code, msg)."""
    if mode == "default":
        if fp.is_dir():
            mode = "reveal"
        elif fp.suffix.lower() in RUNNABLE_EXT:
            return (400, "this type would run instead of open: use Show in folder, or edit it in the Files tab")
    try:
        if os.name == "nt":
            if mode == "default":
                os.startfile(str(fp))
            elif fp.is_dir():
                subprocess.Popen(["explorer", str(fp)])
            else:
                subprocess.Popen('explorer /select,"' + str(fp) + '"')   # paths cannot contain a double quote on Windows
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-R", str(fp)] if mode == "reveal" and fp.is_file() else ["open", str(fp)])
        else:
            subprocess.Popen(["xdg-open", str(fp if mode == "default" or fp.is_dir() else fp.parent)])
    except OSError as e:
        return (500, f"could not open: {type(e).__name__}")
    return None


def upload_file(b):
    """Save a dropped file inside a project folder. Never overwrites unless overwrite is true (old copy is backed up)."""
    pid, rel_dir, name = b.get("project", ""), str(b.get("dir", "")).strip("/\\"), str(b.get("name", ""))
    base = project_dir(pid)
    if not base:
        return (404, "unknown project")
    if (not name or len(name) > 200 or UPLOAD_BAD.search(name) or name in (".", "..") or name != name.strip()
            or name.endswith(".") or is_secret(name) or name in SKIP_DIRS or name.lower().endswith(".av-tmp")
            or name.split(".")[0].lower() in RESERVED):
        return (400, "bad or refused file name")
    folder = confine(pid, rel_dir) if rel_dir else Path(os.path.realpath(base))
    if not folder or not folder.is_dir():
        return (404, "target folder not available")
    try:
        data = base64.b64decode(b.get("data", ""), validate=True)
    except (ValueError, TypeError):
        return (400, "bad data")
    if len(data) > MAX_UPLOAD:
        return (413, "file too large (max 25 MB)")
    target = folder / name
    if os.path.realpath(target) != os.path.join(os.path.realpath(folder), name):
        return (400, "bad file name")
    if target.exists():
        if b.get("overwrite") is not True:
            return (409, "a file with this name already exists in that folder")
        if not target.is_file():
            return (400, "target is a folder")
        bdir = OUT / "backups" / re.sub(r"[^A-Za-z0-9._-]", "_", pid)
        bdir.mkdir(parents=True, exist_ok=True)
        (bdir / f"{time.strftime('%Y%m%d-%H%M%S')}_{name}").write_bytes(target.read_bytes())
    tmp = folder / (name + ".av-tmp")
    tmp.write_bytes(data)
    os.replace(tmp, target)
    log(f"uploaded {pid}/{(rel_dir + '/' if rel_dir else '')}{name} ({len(data)} bytes)")
    return {"path": target.relative_to(os.path.realpath(base)).as_posix(), "size": len(data)}


def sched_jobs():
    """Windows Task Scheduler tasks whose command points at a scanned file. Returns (jobs, note)."""
    if os.name != "nt":
        return [], "Task Scheduler is Windows only"
    try:
        res = subprocess.run(["schtasks", "/query", "/fo", "csv", "/v"], capture_output=True, text=True,
                             timeout=90, encoding="utf-8", errors="replace")
    except (OSError, subprocess.SubprocessError) as e:
        return [], f"schtasks failed ({type(e).__name__})"
    import csv
    import io
    rows = list(csv.reader(io.StringIO(res.stdout)))
    if not rows:
        return [], "schtasks returned nothing"
    hdr = rows[0]
    col = {h: i for i, h in enumerate(hdr)}
    if "TaskName" not in col or "Task To Run" not in col:
        return [], "unexpected schtasks columns (English Windows expected)"
    norm = lambda p: os.path.normcase(os.path.normpath(p))
    index = {}
    for n in graph()["files"].values():
        pdir = project_dir(n["project"])
        if pdir:
            index[norm(os.path.join(pdir, n["path"]))] = n
    get = lambda r, k: r[col[k]] if k in col and len(r) > col[k] else ""
    jobs, seen = [], set()
    for r in rows[1:]:
        name = get(r, "TaskName")
        if not name or name == "TaskName" or name in seen:
            continue
        cmd = get(r, "Task To Run")
        hits = [index[norm(m)] for m in re.findall(r"[A-Za-z]:\\[^\"]*?\.(?:py|js|ps1|bat|cmd)", cmd) if norm(m) in index]
        if not hits:
            continue
        seen.add(name)
        state = get(r, "Scheduled Task State")
        jobs.append({"name": name, "label": name.strip("\\"), "projects": sorted({h["project"] for h in hits}),
                     "script": hits[0]["path"], "schedule": get(r, "Schedule Type"), "next": get(r, "Next Run Time"),
                     "last": get(r, "Last Run Time"), "result": get(r, "Last Result"), "status": get(r, "Status"),
                     "enabled": state.lower() != "disabled", "cmd": cmd[:300]})
    jobs.sort(key=lambda j: j["label"].lower())
    return jobs, ""


def job_action(b):
    act = b.get("action")
    if act not in ("run", "enable", "disable") or b.get("confirm") is not True:
        return (400, "needs action (run, enable, disable) and confirm")
    jobs, note = sched_jobs()
    job = next((j for j in jobs if j["name"] == b.get("name")), None)
    if not job:
        return (404, note or "unknown job (only tasks that run a scanned file can be controlled)")
    argv = ["schtasks", "/run", "/tn", job["name"]] if act == "run" else ["schtasks", "/change", "/tn", job["name"], "/enable" if act == "enable" else "/disable"]
    try:
        res = subprocess.run(argv, capture_output=True, text=True, timeout=30, encoding="utf-8", errors="replace")
    except (OSError, subprocess.SubprocessError) as e:
        return (500, f"schtasks failed ({type(e).__name__})")
    out = (res.stdout.strip() + " " + res.stderr.strip()).strip()[:400]
    log(f"job {act} {job['name']}: exit {res.returncode}")
    if res.returncode != 0:
        return (500, out or f"schtasks exit {res.returncode}")
    return {"ok": True, "output": out}


# ------------------------------------------------------------------ self-update (no git needed)
# Downloads the newest tagged release of the public repo as a zip and replaces the app files only.
# Never touched: scanner/config.local.json, out/, any *.local.* file, project folders, CLAUDE.md, Brief/, Handover/.
UPDATE_REPO = "dnizdz/Agentic_View"
TAG_RE = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")
MAX_ZIP = 20_000_000
UPDATE_DIRS = ("scanner/", "server/", "viewer/")
UPDATE_ROOT = re.compile(r"^(README\.md|CHANGELOG\.md|VERSION|LICENSE|launcher\.py|create_shortcut\.py|INSTALL\.md|CLAUDE\.example\.md|architecture\.md|\.gitignore|\.gitattributes|start_[a-z]+\.(bat|command|sh))$")
UPDATE_SKIP = re.compile(r"(^|/)(__pycache__/|[^/]*\.local\.[^/]*$|[^/]*\.pyc$)")
OBSOLETE = ("start.bat", "start.sh")   # replaced by the per-OS launchers in 1.1.0
U_LOCK = threading.Lock()


def vtuple(s):
    m = TAG_RE.match(s or "") or re.match(r"^()(\d+)\.(\d+)\.(\d+)$", s or "")
    return tuple(int(x) for x in m.groups()[-3:]) if m else None


def http_get(url, limit):
    req = urllib.request.Request(url, headers={"User-Agent": "AgenticView-updater"})
    with urllib.request.urlopen(req, timeout=30) as r:
        data = r.read(limit + 1)
    if len(data) > limit:
        raise ValueError("download too large")
    return data


def latest_tag():
    tags = json.loads(http_get(f"https://api.github.com/repos/{UPDATE_REPO}/tags?per_page=100", 2_000_000))
    found = [(vtuple(t["name"]), t["name"]) for t in tags if TAG_RE.match(str(t.get("name", "")))]
    return max(found)[1] if found else None


def disk_version():
    """Version of the files on disk (changes right after an update, before the server restarts)."""
    try:
        return (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    except OSError:
        return VERSION


def restart_soon():
    """Stop live sessions and restart this server on the same port with the new files.
    Under launcher.py (AV_LAUNCHER=1) exit with code 75 and the launcher starts it again.
    Started any other way, start a fresh copy ourselves (its bind retries until this process is gone)."""
    def go():
        time.sleep(1.5)   # let the HTTP response reach the page first
        for r in list(S["runs"].values()):
            r.stop()
        log("restarting")
        if os.environ.get("AV_LAUNCHER") == "1":
            os._exit(75)
        argv = [sys.executable, "-I", str(ROOT / "server" / "server.py")] + [a for a in sys.argv[1:] if a != "--open"]
        kw = {"cwd": str(ROOT)}
        if os.name == "nt":
            kw["creationflags"] = subprocess.CREATE_NEW_CONSOLE   # its own window, so it can be seen and stopped
        else:
            kw["start_new_session"] = True
        try:
            subprocess.Popen(argv, **kw)
        except OSError as e:
            log(f"restart failed ({type(e).__name__}): start the server again by hand")
            return
        os._exit(0)
    threading.Thread(target=go, daemon=True).start()


def update_check():
    cur = disk_version()
    out = {"current": cur, "latest": None, "newer": False, "isClone": (ROOT / ".git").exists(), "repo": UPDATE_REPO, "error": ""}
    try:
        tag = latest_tag()
    except (OSError, ValueError) as e:
        out["error"] = f"could not reach GitHub ({type(e).__name__}). Check the internet connection."
        if "CERTIFICATE_VERIFY_FAILED" in str(e):
            out["error"] += " On macOS, run 'Install Certificates.command' from your Python folder in Applications, then try again."
        return out
    if tag:
        out["latest"] = tag[1:]
        out["newer"] = bool(vtuple(cur) and vtuple(tag) > vtuple(cur))
    else:
        out["error"] = "no released version found"
    return out


def update_apply(b):
    ver = str(b.get("version", ""))
    if b.get("confirm") is not True or not TAG_RE.match(ver):
        return (400, "needs confirm and a version tag like v1.2.0")
    if (ROOT / ".git").exists():
        return (409, "this folder is a git clone: update it with git pull instead")
    cur = disk_version()
    if not vtuple(cur) or vtuple(ver) <= vtuple(cur):
        return (409, "already up to date")
    if not U_LOCK.acquire(blocking=False):
        return (409, "an update is already running")
    try:
        import io
        import zipfile
        try:
            data = http_get(f"https://github.com/{UPDATE_REPO}/archive/refs/tags/{ver}.zip", MAX_ZIP)
            zf = zipfile.ZipFile(io.BytesIO(data))
        except (OSError, ValueError, zipfile.BadZipFile) as e:
            hint = " On macOS, run 'Install Certificates.command' from your Python folder in Applications." if "CERTIFICATE_VERIFY_FAILED" in str(e) else ""
            return (502, f"download failed ({type(e).__name__}). Nothing was changed." + hint)
        names = zf.namelist()
        prefix = names[0].split("/")[0] + "/" if names else ""
        if not prefix or sum(i.file_size for i in zf.infolist()) > 60_000_000 or any(
                not n.startswith(prefix) or ".." in n.split("/") or n.startswith("/") or "\\" in n or ":" in n for n in names):
            return (502, "unexpected archive layout. Nothing was changed.")
        files = {}
        for n in names:
            rel = n[len(prefix):]
            if not rel or n.endswith("/") or UPDATE_SKIP.search(rel):
                continue
            if rel.startswith(UPDATE_DIRS) or UPDATE_ROOT.match(rel):
                files[rel] = zf.read(n)
        need = ("VERSION", "server/server.py", "scanner/scan.py", "viewer/index.html")
        if any(k not in files for k in need) or files["VERSION"].decode("utf-8", "replace").strip() != ver[1:]:
            return (502, "archive does not match the requested version. Nothing was changed.")
        for rel, raw in files.items():       # refuse to install code that does not even compile
            if rel.endswith(".py"):
                try:
                    compile(raw, rel, "exec")
                except SyntaxError:
                    return (502, f"{rel} in the download has a syntax error. Nothing was changed.")
        bdir = OUT / "backups" / f"update-{cur}-{time.strftime('%Y%m%d-%H%M%S')}"
        pending, done = [], 0
        for rel, raw in sorted(files.items()):
            target = ROOT / rel
            if target.exists() and target.read_bytes().replace(b"\r\n", b"\n") == raw.replace(b"\r\n", b"\n"):
                continue   # unchanged (also keeps a launcher that may be running untouched)
            if target.exists():
                (bdir / rel).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(target, bdir / rel)
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp = target.with_name(target.name + ".av-new")
            tmp.write_bytes(raw)
            if rel.endswith((".sh", ".command")) and os.name != "nt":
                os.chmod(tmp, 0o755)
            try:
                os.replace(tmp, target)
                done += 1
            except OSError:                  # e.g. the .bat that is running right now is locked on Windows
                pending.append(rel)
        if not pending:
            for old in OBSOLETE:
                if (ROOT / old).exists() and (ROOT / "start_windows.bat").exists():
                    try:
                        os.remove(ROOT / old)
                    except OSError:
                        pass
        log(f"updated {cur} -> {ver[1:]}: {done} files, {len(pending)} pending")
        restart_soon()
        return {"updated": ver[1:], "files": done, "pending": pending, "restarting": True,
                "backup": str(bdir.relative_to(ROOT)).replace("\\", "/")}
    finally:
        U_LOCK.release()


# ------------------------------------------------------------------ handler
class H(BaseHTTPRequestHandler):
    server_version = "AgenticView"

    def log_message(self, fmt, *args):
        pass

    # ---- helpers
    def _hosts_ok(self):
        ok_hosts = {f"127.0.0.1:{S['port']}", f"localhost:{S['port']}"}
        if self.headers.get("Host", "") not in ok_hosts:
            return False
        origin = self.headers.get("Origin")
        return origin is None or origin in {f"http://{h}" for h in ok_hosts}

    def _send(self, code, body, ctype="application/json", extra=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode("utf-8")
        elif isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype + ("; charset=utf-8" if ctype.startswith("text") or ctype == "application/json" else ""))
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _err(self, code, msg):
        self._send(code, {"error": msg})

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n > (MAX_UPLOAD * 4 // 3 + 4096 if self.path.split("?")[0] == "/api/upload" else MAX_BODY):
            return None
        try:
            return json.loads(self.rfile.read(n).decode("utf-8") or "{}")
        except ValueError:
            return None

    def _token_ok(self):
        return secrets.compare_digest(self.headers.get("X-AV-Token", ""), S["token"])

    def _cookie_ok(self):
        for part in self.headers.get("Cookie", "").split(";"):
            k, _, v = part.strip().partition("=")
            if k == "av" and secrets.compare_digest(v, S["token"]):
                return True
        return False

    # ---- routing
    def do_GET(self):
        if not self._hosts_ok():
            return self._err(403, "bad host")
        u = urlparse(self.path)
        q = parse_qs(u.query)
        p = u.path
        if p in ("/", "/index.html"):
            html = (VIEWER / "index.html").read_text(encoding="utf-8")
            boot = ("<script>window.AV=" + json.dumps({"token": S["token"], "readOnly": S["read_only"],
                                                      "allowBypass": S["allow_bypass"], "version": VERSION}) + ";</script>")
            html = html.replace("<!--AV_BOOT-->", boot)
            return self._send(200, html, "text/html", {"Set-Cookie": f"av={S['token']}; HttpOnly; SameSite=Strict; Path=/"})
        if p.startswith("/out/"):
            name = p[5:]
            if name not in OUT_FILES or not self._cookie_ok():
                return self._err(403, "forbidden")
            f = OUT / name
            if not f.exists():
                return self._err(404, "run the scanner first")
            ctype = {"graph.js": "application/javascript", "graph.json": "application/json",
                     "impact.json": "application/json", "INDEX.md": "text/markdown"}[name]
            return self._send(200, f.read_bytes(), ctype)
        if not p.startswith("/api/"):
            return self._err(404, "not found")
        if not self._token_ok():
            return self._err(401, "bad token")
        try:
            return self._api_get(p, q)
        except Exception as e:  # keep the server alive, report the class only
            log(f"error {p}: {type(e).__name__}")
            return self._err(500, type(e).__name__)

    def do_POST(self):
        if not self._hosts_ok():
            return self._err(403, "bad host")
        if not self._token_ok():
            return self._err(401, "bad token")
        body = self._body()
        if body is None:
            return self._err(400, "bad body")
        try:
            return self._api_post(urlparse(self.path).path, body)
        except Exception as e:
            log(f"error {self.path}: {type(e).__name__}")
            return self._err(500, type(e).__name__)

    do_PUT = do_POST

    # ---- GET api
    def _api_get(self, p, q):
        g = lambda k: (q.get(k) or [""])[0]
        if p == "/api/state":
            cb = claude_bin()
            ver = run_capture([cb, "--version"]) if cb else ""
            auth = {}
            if cb:
                try:
                    auth = json.loads(run_capture([cb, "auth", "status"]) or "{}")
                except ValueError:
                    auth = {}
            cmd, _ = install_spec()
            return self._send(200, {"claude": {"installed": bool(cb), "path": cb or "", "version": ver,
                                               "loggedIn": bool(auth.get("loggedIn")), "auth": {k: auth.get(k) for k in ("authMethod", "apiProvider")}},
                                    "install": {"command": cmd, "os": os.name},
                                    "readOnly": S["read_only"], "allowBypass": S["allow_bypass"],
                                    "meta": graph()["meta"]})
        if p == "/api/project":
            pid = g("id")
            if not project_dir(pid):
                return self._err(404, "unknown project")
            return self._send(200, {"settings": project_settings(pid)})
        if p == "/api/files":
            pid = g("project")
            base = project_dir(pid)
            if not base:
                return self._err(404, "unknown project")
            out = []
            for cur, dn, fn in os.walk(base):
                dn[:] = [d for d in dn if d not in SKIP_DIRS and d not in CFG["archive_dir_names"]]
                for f in fn:
                    if is_secret(f):
                        continue
                    fp = Path(cur) / f
                    try:
                        sz = fp.stat().st_size
                    except OSError:
                        continue
                    out.append({"path": fp.relative_to(base).as_posix(), "size": sz})
                    if len(out) >= 3000:
                        break
                if len(out) >= 3000:
                    break
            out.sort(key=lambda x: x["path"].lower())
            return self._send(200, {"files": out, "truncated": len(out) >= 3000})
        if p == "/api/file":
            fp = confine(g("project"), g("path"))
            if not fp or not fp.is_file():
                return self._err(404, "not available")
            if fp.stat().st_size > MAX_FILE:
                return self._err(413, "file too large")
            raw = fp.read_bytes()
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError:
                return self._err(415, "not a UTF-8 text file")
            return self._send(200, {"content": text.replace("\r\n", "\n"), "hash": hashlib.sha1(raw).hexdigest(),
                                    "crlf": "\r\n" in text})
        if p == "/api/scripts":
            pid = g("project")
            res = [{"id": n["id"], "path": n["path"], "lang": n["lang"]} for n in graph()["files"].values()
                   if n["project"] == pid and not n.get("archived") and n["lang"] in ("python", "js", "shell", "ts")
                   and Path(n["path"]).suffix.lower() in scripts_ext()]
            res.sort(key=lambda x: x["path"].lower())
            return self._send(200, {"scripts": res})
        if p == "/api/update/check":
            return self._send(200, update_check())
        if p == "/api/jobs":
            pid = g("project")
            jobs, note = sched_jobs()
            if pid:
                jobs = [j for j in jobs if pid in j["projects"]]
            return self._send(200, {"jobs": jobs, "note": note, "readOnly": S["read_only"]})
        if p == "/api/global-claude":
            raw = GLOBAL_MD.read_bytes() if GLOBAL_MD.exists() else b""
            if len(raw) > MAX_FILE:
                return self._err(413, "file too large")
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError:
                return self._err(415, "not a UTF-8 text file")
            return self._send(200, {"path": str(GLOBAL_MD), "exists": GLOBAL_MD.exists(), "content": text.replace("\r\n", "\n"),
                                    "hash": hashlib.sha1(raw).hexdigest() if raw else "", "crlf": "\r\n" in text, "readOnly": S["read_only"]})
        if p == "/api/runs":
            with S_LOCK:
                return self._send(200, {"runs": [r.info() for r in S["runs"].values()]})
        m = re.match(r"^/api/run/([0-9a-f]{12})/stream$", p)
        if m:
            return self._stream(m.group(1), int(g("from") or 0))
        return self._err(404, "not found")

    def _stream(self, rid, start):
        r = S["runs"].get(rid)
        if not r:
            return self._err(404, "unknown run")
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        i = start
        try:
            while True:
                with r.cond:
                    while i >= len(r.chunks) and not r.done:
                        r.cond.wait(timeout=10)
                        if i >= len(r.chunks) and not r.done:
                            break
                    batch = r.chunks[i:]
                    done = r.done and i + len(batch) >= len(r.chunks)
                if batch:
                    for t in batch:
                        self.wfile.write(("data: " + json.dumps({"i": i, "t": t}) + "\n\n").encode("utf-8"))
                        i += 1
                    self.wfile.flush()
                elif not done:
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
                if done:
                    self.wfile.write(("event: end\ndata: " + json.dumps({"code": r.code}) + "\n\n").encode("utf-8"))
                    self.wfile.flush()
                    return
        except (BrokenPipeError, ConnectionResetError, OSError):
            return

    # ---- POST api
    def _api_post(self, p, b):
        if p == "/api/file":
            if S["read_only"]:
                return self._err(403, "server is read-only")
            fp = confine(b.get("project", ""), b.get("path", ""))
            if not fp or not isinstance(b.get("content"), str):
                return self._err(400, "not available")
            content = b["content"]
            if len(content.encode("utf-8")) > MAX_FILE:
                return self._err(413, "too large")
            old_raw = fp.read_bytes() if fp.exists() else None
            if (hashlib.sha1(old_raw).hexdigest() if old_raw is not None else "") != b.get("hash", ""):
                return self._err(409, "file changed on disk since it was loaded")
            if old_raw is not None and b"\r\n" in old_raw:
                content = content.replace("\r\n", "\n").replace("\n", "\r\n")
            if old_raw is not None:
                bdir = OUT / "backups" / re.sub(r"[^A-Za-z0-9._-]", "_", b["project"])
                bdir.mkdir(parents=True, exist_ok=True)
                (bdir / f"{time.strftime('%Y%m%d-%H%M%S')}_{fp.name}").write_bytes(old_raw)
            fp.parent.mkdir(parents=True, exist_ok=True)
            tmp = fp.with_name(fp.name + ".av-tmp")
            tmp.write_bytes(content.encode("utf-8"))
            os.replace(tmp, fp)
            log(f"saved {b['project']}/{b['path']}")
            return self._send(200, {"hash": hashlib.sha1(content.encode("utf-8")).hexdigest()})

        if p == "/api/open":
            fp = confine(b.get("project", ""), b.get("path", "")) if b.get("path") else project_dir(b.get("project", ""))
            mode = b.get("mode")
            if mode not in ("default", "reveal") or not fp or not fp.exists():
                return self._err(400, "not available")
            if mode == "default" and S["read_only"]:
                return self._err(403, "server is read-only")
            err = open_path(fp, mode)
            if err:
                return self._err(*err)
            return self._send(200, {"ok": True})

        if p == "/api/upload":
            if S["read_only"]:
                return self._err(403, "server is read-only")
            r = upload_file(b)
            if isinstance(r, tuple):
                return self._err(*r)
            return self._send(200, r)

        if p == "/api/update":
            if S["read_only"]:
                return self._err(403, "server is read-only")
            r = update_apply(b)
            if isinstance(r, tuple):
                return self._err(*r)
            return self._send(200, r)

        if p == "/api/job":
            if S["read_only"]:
                return self._err(403, "server is read-only")
            r = job_action(b)
            if isinstance(r, tuple):
                return self._err(*r)
            return self._send(200, r)

        m = re.match(r"^/api/run/([0-9a-f]{12})/permit$", p)
        if m:
            r = S["runs"].get(m.group(1))
            if not r or r.kind != "session":
                return self._err(404, "unknown session")
            if b.get("decision") not in ("allow", "allow_session", "deny") or not isinstance(b.get("request_id"), str):
                return self._err(400, "needs request_id and decision")
            if b["decision"] != "deny" and S["read_only"]:
                return self._err(403, "server is read-only")
            e = r.permit(b["request_id"], b["decision"])
            return self._err(*e) if e else self._send(200, {"ok": True})

        m = re.match(r"^/api/run/([0-9a-f]{12})/(stdin|stop|interrupt)$", p)
        if m:
            r = S["runs"].get(m.group(1))
            if not r:
                return self._err(404, "unknown run")
            if m.group(2) == "stop":
                r.stop()
            elif m.group(2) == "interrupt":
                if r.kind != "session":
                    return self._err(403, "interrupt is for chat sessions only")
                if r.done:
                    return self._err(409, "session has ended")
                r.send(json.dumps({"type": "control_request", "request_id": "int_" + uuid.uuid4().hex[:8], "request": {"subtype": "interrupt"}}) + "\n")
            elif r.kind == "login":
                r.send(str(b.get("text", ""))[:4000])
            elif r.kind == "session":
                text = str(b.get("text", ""))
                if not text.strip() or len(text) > 50000:
                    return self._err(400, "empty or too long")
                if r.done:
                    return self._err(409, "session has ended")
                r.send(json.dumps({"type": "user", "message": {"role": "user", "content": [{"type": "text", "text": text}]}}) + "\n")
            else:
                return self._err(403, "stdin not accepted for this run")
            return self._send(200, {"ok": True})

        if p == "/api/global-claude":
            if S["read_only"]:
                return self._err(403, "server is read-only")
            if b.get("confirm") is not True or not isinstance(b.get("content"), str):
                return self._err(400, "needs content and confirm")
            content = b["content"]
            if len(content.encode("utf-8")) > MAX_FILE:
                return self._err(413, "too large")
            old = GLOBAL_MD.read_bytes() if GLOBAL_MD.exists() else None
            if (hashlib.sha1(old).hexdigest() if old else "") != b.get("hash", ""):
                return self._err(409, "file changed on disk since it was loaded")
            if old is not None and b"\r\n" in old:
                content = content.replace("\r\n", "\n").replace("\n", "\r\n")
            if old is not None:
                bdir = OUT / "backups" / "global"
                bdir.mkdir(parents=True, exist_ok=True)
                (bdir / f"{time.strftime('%Y%m%d-%H%M%S')}_CLAUDE.md").write_bytes(old)
            GLOBAL_MD.parent.mkdir(parents=True, exist_ok=True)
            tmp = GLOBAL_MD.with_name("CLAUDE.md.av-tmp")
            tmp.write_bytes(content.encode("utf-8"))
            os.replace(tmp, GLOBAL_MD)
            log("saved global CLAUDE.md")
            return self._send(200, {"hash": hashlib.sha1(content.encode("utf-8")).hexdigest()})

        if p == "/api/project/new":
            if S["read_only"]:
                return self._err(403, "server is read-only")
            r = new_project(b)
            if isinstance(r, tuple):
                return self._err(*r)
            return self._send(200, r)

        if p != "/api/run":
            return self._err(404, "not found")
        kind = b.get("kind")
        if kind == "scan":
            r = start_run("scan", "rescan", [sys.executable, "-I", str(ROOT / "scanner" / "scan.py")], ROOT)
        elif kind == "chat":
            r = self._start_chat(b)
        elif kind == "session":
            r = self._start_chat(b, persistent=True)
        elif kind == "script":
            r = self._start_script(b)
        elif kind == "login":
            cb = claude_bin()
            if not cb:
                return self._err(400, "claude not installed")
            r = start_run("login", "claude auth login", [cb, "auth", "login"], ROOT, keep_stdin=True)
        elif kind == "install":
            if S["read_only"]:
                return self._err(403, "server is read-only")
            cmd, argv = install_spec()
            if b.get("confirm_command") != cmd:
                return self._err(400, "confirm_command must equal the displayed command")
            r = start_run("install", cmd, argv, ROOT)
        else:
            return self._err(400, "unknown kind")
        if isinstance(r, tuple):
            return self._err(*r)
        return self._send(200, r.info())

    def _start_chat(self, b, persistent=False):
        cb = claude_bin()
        if not cb:
            return (400, "claude not installed")
        base = project_dir(b.get("project", ""))
        prompt = b.get("prompt", "")
        if not base or not isinstance(prompt, str) or len(prompt) > 50000 or (not persistent and not prompt.strip()):
            return (400, "bad project or prompt")
        mode = b.get("mode", "project")
        argv = [cb, "-p", "--output-format", "stream-json", "--verbose", "--include-partial-messages"]
        if persistent:
            argv += ["--input-format", "stream-json", "--replay-user-messages"]
        if S["read_only"] and mode != "plan":
            return (403, "server is read-only: only plan mode is allowed")
        if mode in ("acceptEdits", "plan"):
            argv += ["--permission-mode", mode]
        elif mode == "bypass":
            if not S["allow_bypass"]:
                return (403, "start the server with --allow-bypass to enable this")
            if b.get("confirm_bypass") is not True:
                return (400, "bypass needs confirm_bypass")
            argv.append("--dangerously-skip-permissions")
        elif mode != "project":
            return (400, "unknown mode")
        if S["allow_edit"] and mode != "plan" and mode != "bypass":
            argv += ["--allowedTools", *ALLOW_EDIT_TOOLS]
        ctx = b.get("context", "full")
        if ctx == "lean":
            argv += ["--setting-sources", "project,local"]
        elif ctx != "full":
            return (400, "unknown context")
        sid = b.get("session_id")
        if sid:
            if not SID_RE.match(str(sid)):
                return (400, "bad session id")
            argv += ["--resume", str(sid)]
        model = b.get("model")
        if model:
            if not MODEL_RE.match(str(model)):
                return (400, "bad model")
            argv += ["--model", str(model)]
        if persistent and mode in ("project", "acceptEdits"):
            argv += ["--permission-prompt-tool", "stdio"]   # the page answers tool prompts (Approve / Deny cards)
        if persistent:
            if sum(1 for r in S["runs"].values() if r.kind == "session" and not r.done) >= 6:
                return (429, "too many live sessions: end one first")
            r = start_run("session", f"{b['project']} [{mode}]", argv, base, keep_stdin=True)
            r.project = b["project"]
            r.meta = {"mode": mode, "context": ctx, "model": str(model or "")}
            return r
        return start_run("chat", f"{b['project']} [{mode}]", argv, base, stdin_text=prompt)

    def _start_script(self, b):
        if S["read_only"]:
            return (403, "server is read-only")
        n = graph()["files"].get(b.get("file_id", ""))
        if not n or n.get("archived") or b.get("confirm") is not True:
            return (400, "unknown script or missing confirm")
        base = project_dir(n["project"])
        fp = confine(n["project"], n["path"])
        ext = Path(n["path"]).suffix.lower()
        if not base or not fp or ext not in scripts_ext():
            return (400, "not runnable")
        if ext == ".py":
            argv = [shutil.which("python") or sys.executable, "-u", str(fp)]
        elif ext in (".js", ".mjs", ".cjs"):
            argv = [shutil.which("node") or "node", str(fp)]
        elif ext == ".ps1":
            argv = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(fp)]
        else:
            argv = ["cmd", "/c", str(fp)]
        return start_run("script", n["id"], argv, fp.parent)


def scripts_ext():
    return {".py", ".js", ".mjs", ".cjs", ".ps1"} | ({".bat", ".cmd"} if os.name == "nt" else set())


def main():
    ap = argparse.ArgumentParser(description="Agentic_View local server (127.0.0.1 only)")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--open", action="store_true", help="open the browser")
    ap.add_argument("--read-only", action="store_true", help="no file edits, no script runs, no install, plan-mode chat only")
    ap.add_argument("--allow-bypass", action="store_true", help="allow the chat toggle that skips all Claude permission checks")
    ap.add_argument("--allow-edit", action="store_true", help="chat may also run python without asking (on top of Accept edits); ignored with --read-only")
    a = ap.parse_args()
    S.update(token=secrets.token_urlsafe(32), port=a.port, read_only=a.read_only, allow_bypass=a.allow_bypass and not a.read_only,
             allow_edit=a.allow_edit and not a.read_only)
    srv = None
    for attempt in range(40):   # after an update restart the old process may still hold the port for a moment
        try:
            srv = ThreadingHTTPServer(("127.0.0.1", a.port), H)
            break
        except OSError:
            if attempt == 0:
                log(f"port {a.port} is busy, waiting for it to free up")
            time.sleep(0.5)
    if srv is None:
        log(f"port {a.port} stays busy: stop the other program or start with --port <number>")
        sys.exit(1)
    srv.daemon_threads = True
    url = f"http://127.0.0.1:{a.port}/"
    log(f"Agentic View {VERSION} server on {url}  (read-only={S['read_only']}, allow-edit={S['allow_edit']}, allow-bypass={S['allow_bypass']})")
    log("Local only. Press Ctrl+C to stop.")
    if a.open:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        for r in list(S["runs"].values()):
            r.stop()
        log("stopped")


if __name__ == "__main__":
    main()
