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
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "out"
VIEWER = ROOT / "viewer"
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

S = {"token": "", "port": 0, "read_only": False, "allow_bypass": False,
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


# ------------------------------------------------------------------ claude
def claude_bin():
    found = shutil.which("claude")
    if found:
        return found
    cand = Path.home() / ".local" / "bin" / ("claude.exe" if os.name == "nt" else "claude")
    return str(cand) if cand.exists() else None


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

    def _push(self, text):
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
                "code": self.code, "started": int(self.started)}


def start_run(*a, **kw):
    r = Run(*a, **kw)
    with S_LOCK:
        S["runs"][r.id] = r
    log(f"run {r.id} ({r.kind}: {r.label}) started")
    return r


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
        if n > MAX_BODY:
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
                                                      "allowBypass": S["allow_bypass"]}) + ";</script>")
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

        m = re.match(r"^/api/run/([0-9a-f]{12})/(stdin|stop)$", p)
        if m:
            r = S["runs"].get(m.group(1))
            if not r:
                return self._err(404, "unknown run")
            if m.group(2) == "stop":
                r.stop()
            else:
                if r.kind != "login":
                    return self._err(403, "stdin only for login runs")
                r.send(str(b.get("text", ""))[:4000])
            return self._send(200, {"ok": True})

        if p != "/api/run":
            return self._err(404, "not found")
        kind = b.get("kind")
        if kind == "scan":
            r = start_run("scan", "rescan", [sys.executable, "-I", str(ROOT / "scanner" / "scan.py")], ROOT)
        elif kind == "chat":
            r = self._start_chat(b)
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

    def _start_chat(self, b):
        cb = claude_bin()
        if not cb:
            return (400, "claude not installed")
        base = project_dir(b.get("project", ""))
        prompt = b.get("prompt", "")
        if not base or not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 50000:
            return (400, "bad project or prompt")
        mode = b.get("mode", "project")
        argv = [cb, "-p", "--output-format", "stream-json", "--verbose", "--include-partial-messages"]
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
    a = ap.parse_args()
    S.update(token=secrets.token_urlsafe(32), port=a.port, read_only=a.read_only, allow_bypass=a.allow_bypass and not a.read_only)
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), H)
    srv.daemon_threads = True
    url = f"http://127.0.0.1:{a.port}/"
    log(f"Agentic View server on {url}  (read-only={S['read_only']}, allow-bypass={S['allow_bypass']})")
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
