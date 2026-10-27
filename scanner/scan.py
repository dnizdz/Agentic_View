#!/usr/bin/env python3
"""Agentic_View scanner. Standard library only, read-only.

Walks a folder of projects (each immediate subfolder = one project), builds a
dependency graph and writes:
  out/graph.json   full map (nodes, edges, hygiene)
  out/graph.js     same data for the offline viewer (window.AGENTIC_GRAPH)
  out/impact.json  per file: who depends on it (direct, transitive, projects, jobs)
  out/INDEX.md     one section per project, for agents to grep

Secrets: .env files are never opened. Only env key NAMES seen in code and URL
hosts are stored. Output is self-checked for secret-looking strings.
"""
import argparse
import ast
import fnmatch
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from collections import defaultdict, deque
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT_DIR = HERE.parent / "out"

DEFAULT_CONFIG = {
    "root": ".",
    "exclude_projects": [],
    "exclude_dir_names": [
        "node_modules", ".git", ".venv", "venv", "env", "__pycache__", "dist",
        "build", ".next", ".claude", "site-packages", ".profile-chrome",
        "profile", ".cache", ".idea", ".vscode",
    ],
    "exclude_file_globs": [".env", ".env.*", "*.env", "token*.json",
                           "credentials*.json", "*.pem", "*.key", ".clasprc*"],
    "archive_dir_names": ["Archive", "archive"],
    "include_archive": False,
    "extra_roots": [],
    "schedule": False,
    "max_text_bytes": 1500000,
    "host_aliases": {},
    "ignore_hosts": [
        "localhost", "127.0.0.1", "0.0.0.0", "example.com", "www.w3.org",
        "schema.org", "cdnjs.cloudflare.com", "cdn.jsdelivr.net", "unpkg.com",
        "fonts.googleapis.com", "fonts.gstatic.com", "github.com",
        "raw.githubusercontent.com", "www.googleapis.com", "stackoverflow.com",
        "schemas.openxmlformats.org", "schemas.microsoft.com", "purl.org",
        "www.apache.org", "opensource.org", "developer.mozilla.org",
    ],
}

LANG = {".py": "python", ".js": "js", ".mjs": "js", ".cjs": "js", ".jsx": "js",
        ".ts": "ts", ".tsx": "ts", ".gs": "appsscript", ".bat": "shell",
        ".cmd": "shell", ".ps1": "shell"}
JS_EXT = [".js", ".mjs", ".cjs", ".jsx", ".ts", ".tsx", ".gs"]
RUNNABLE = (".py", ".js", ".ps1", ".bat", ".cmd", ".gs", ".mjs")

URL_RE = re.compile(r"https?://([A-Za-z0-9][A-Za-z0-9.-]*\.[A-Za-z]{2,}|\d{1,3}(?:\.\d{1,3}){3})")
SYSTEM_PATTERNS = [
    ("system:odoo", re.compile(r"xmlrpc|/jsonrpc|odoo", re.I)),
    ("system:google-sheets", re.compile(r"gspread|SpreadsheetApp|sheets\.googleapis")),
    ("system:gmail", re.compile(r"GmailApp|gmail\.googleapis")),
    ("system:google-drive", re.compile(r"DriveApp|drive\.googleapis")),
    ("system:claude-cli", re.compile(r"""["']claude(?:\.exe|\.cmd)?["']""")),
    ("system:gam", re.compile(r"\bgam(?:\.exe)?\s+(?:user|update|create|print|info)", re.I)),
    ("system:ssh", re.compile(r"\b(?:plink|ssh)(?:\.exe)?\b\s+\S*@?\d")),
]
ENV_PY_RE = re.compile(r"""(?:os\.environ(?:\.get)?\s*[\[(]\s*|os\.getenv\s*\(\s*)["']([A-Z][A-Z0-9_]{2,})["']""")
ENV_JS_RE = re.compile(r"process\.env\.([A-Z][A-Z0-9_]{2,})")
SECRET_PATTERNS = [
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"sk-[A-Za-z0-9_-]{20,}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{30,}"),
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r"AIza[0-9A-Za-z_-]{30,}"),
    re.compile(r"(?i)(?:password|passwd|secret|token|api[_-]?key)\"?\s*[:=]\s*\"?[A-Za-z0-9+/_-]{16,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
]


def log(msg):
    print(msg, flush=True)


# ---------------------------------------------------------------- config
def load_config(path):
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    if path:
        with open(path, encoding="utf-8") as f:
            cfg.update(json.load(f))
    return cfg


# ---------------------------------------------------------------- discovery
def norm(p):
    return os.path.normcase(os.path.normpath(str(p)))


def is_excluded_file(name, cfg):
    return any(fnmatch.fnmatch(name.lower(), g.lower()) for g in cfg["exclude_file_globs"])


def discover(roots, cfg):
    """Return (projects, files). projects: id -> {path, archived, root_label}."""
    projects, files = {}, []
    excl_dirs = set(cfg["exclude_dir_names"])
    arch_dirs = set(cfg["archive_dir_names"])
    excl_projects = set(cfg["exclude_projects"])
    self_dir = norm(HERE.parent)
    for root in roots:
        rpath = Path(root["path"])
        if not rpath.is_dir():
            log(f"skip root (missing): {rpath}")
            continue
        prefix = root.get("prefix", "")
        for pdir in sorted(p for p in rpath.iterdir() if p.is_dir()):
            if pdir.name in excl_projects or pdir.name in excl_dirs or pdir.name.startswith("."):
                continue
            if norm(pdir.resolve()) == self_dir:
                continue  # never scan Agentic_View itself
            pid = prefix + pdir.name
            projects[pid] = {"path": pdir, "archived": bool(root.get("archived"))}
            for cur, dnames, fnames in os.walk(pdir):
                rel_parts = Path(cur).relative_to(pdir).parts
                in_archive = any(part in arch_dirs for part in rel_parts)
                keep = []
                for d in dnames:
                    if d in excl_dirs:
                        continue
                    if d in arch_dirs and not cfg["include_archive"]:
                        continue
                    keep.append(d)
                dnames[:] = keep
                for fn in fnames:
                    ext = os.path.splitext(fn)[1].lower()
                    if ext not in LANG and fn not in ("CLAUDE.md", ".clasp.json", "appsscript.json"):
                        continue
                    if is_excluded_file(fn, cfg):
                        continue
                    files.append({
                        "project": pid, "abs": Path(cur) / fn,
                        "archived": projects[pid]["archived"] or in_archive,
                    })
    return projects, files


# ---------------------------------------------------------------- python path evaluator
def eval_path(node, file_path, env):
    """Best-effort evaluation of a path expression. Returns str or None."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name):
        if node.id == "__file__":
            return str(file_path)
        return env.get(node.id)
    if isinstance(node, ast.BinOp):
        l, r = eval_path(node.left, file_path, env), eval_path(node.right, file_path, env)
        if l is None or r is None:
            return None
        if isinstance(node.op, ast.Div):
            return os.path.join(l, r)
        if isinstance(node.op, ast.Add):
            return l + r
        return None
    if isinstance(node, ast.Attribute):
        base = eval_path(node.value, file_path, env)
        if base is not None and node.attr == "parent":
            return os.path.dirname(base)
        return None
    if isinstance(node, ast.Subscript):
        v = node.value
        if isinstance(v, ast.Attribute) and v.attr == "parents":
            base = eval_path(v.value, file_path, env)
            idx = node.slice
            if base is not None and isinstance(idx, ast.Constant) and isinstance(idx.value, int):
                for _ in range(idx.value + 1):
                    base = os.path.dirname(base)
                return base
        return None
    if isinstance(node, ast.Call):
        f = node.func
        name = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", None)
        if name in ("Path", "PurePath", "PureWindowsPath", "WindowsPath") and node.args:
            parts = [eval_path(a, file_path, env) for a in node.args]
            return os.path.join(*parts) if all(p is not None for p in parts) else None
        if name in ("resolve", "absolute", "expanduser") and isinstance(f, ast.Attribute):
            return eval_path(f.value, file_path, env)
        if name in ("abspath", "realpath", "normpath") and node.args:
            return eval_path(node.args[0], file_path, env)
        if name == "dirname" and node.args:
            b = eval_path(node.args[0], file_path, env)
            return os.path.dirname(b) if b is not None else None
        if name in ("join", "joinpath"):
            args = list(node.args)
            if name == "joinpath" and isinstance(f, ast.Attribute):
                args = [f.value] + args
            parts = [eval_path(a, file_path, env) for a in args]
            return os.path.join(*parts) if parts and all(p is not None for p in parts) else None
        if name == "str" and node.args:
            return eval_path(node.args[0], file_path, env)
    return None


# ---------------------------------------------------------------- per-file parse
def parse_python(text, fpath, with_symbols):
    out = {"imports": [], "sys_path": [], "unresolved_sys_path": 0, "symbols": [], "strings": []}
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        out["parse_error"] = True
        return out
    env = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            v = eval_path(node.value, fpath, env)
            if v is not None:
                env[node.targets[0].id] = v
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                out["imports"].append({"mod": a.name, "names": [], "level": 0, "line": node.lineno})
        elif isinstance(node, ast.ImportFrom):
            out["imports"].append({"mod": node.module or "", "names": [a.name for a in node.names],
                                   "level": node.level, "line": node.lineno})
        elif isinstance(node, ast.Call):
            f = node.func
            if (isinstance(f, ast.Attribute) and f.attr in ("insert", "append")
                    and isinstance(f.value, ast.Attribute) and f.value.attr == "path"
                    and isinstance(f.value.value, ast.Name) and f.value.value.id == "sys"):
                arg = node.args[-1] if node.args else None
                p = eval_path(arg, fpath, env) if arg is not None else None
                if p:
                    out["sys_path"].append({"dir": os.path.normpath(p), "line": node.lineno})
                else:
                    out["unresolved_sys_path"] += 1
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) and 4 <= len(node.value) <= 80:
            out["strings"].append(node.value)
    if with_symbols:
        aliases = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    local = a.asname or a.name.split(".")[0]
                    aliases[local] = {"mod": a.name if a.asname else local, "name": None, "level": 0}
            elif isinstance(node, ast.ImportFrom):
                for a in node.names:
                    aliases[a.asname or a.name] = {"mod": node.module or "", "name": a.name, "level": node.level}
        funcs, top_other = {}, []
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                out["symbols"].append(node.name)
                funcs[node.name] = {"line": node.lineno, "calls": collect_calls([node])}
            else:
                top_other.append(node)
        out["aliases"] = aliases
        out["funcs"] = dict(list(funcs.items())[:400])
        out["module_calls"] = collect_calls(top_other)
    out["symbols"] = out["symbols"][:60]
    return out


def collect_calls(nodes):
    """Distinct call shapes in the given AST nodes: ["n", name, ""] or ["a", base, attr]."""
    calls = set()
    for top in nodes:
        for n in ast.walk(top):
            if isinstance(n, ast.Call):
                f = n.func
                if isinstance(f, ast.Name):
                    calls.add(("n", f.id, ""))
                elif isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name):
                    calls.add(("a", f.value.id, f.attr))
    return [list(c) for c in sorted(calls)][:60]


JS_IMPORT_RE = re.compile(r"""(?:import\s+(?:[^'"]*?\s+from\s+)?|require\s*\(\s*|import\s*\(\s*)["']([^'"]+)["']""")
SCRIPT_REF_RE = re.compile(r"""["']?([A-Za-z]:[\\/][^"'\r\n]*?\.(?:py|js|ps1|bat|cmd|gs)|[\w .\\/~-]*?[\w-]+\.(?:py|js|ps1|bat|cmd|gs))["']?""")


def read_text(path, limit):
    try:
        if path.stat().st_size > limit:
            return None
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


# ---------------------------------------------------------------- scan
def scan(cfg, roots, use_cache=True):
    t0 = time.time()
    projects, files = discover(roots, cfg)
    log(f"found {len(projects)} projects, {len(files)} candidate files")

    cache_path = OUT_DIR / "cache.json"
    cache = {}
    if use_cache and cache_path.exists():
        try:
            cache = json.loads(cache_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            cache = {}
    new_cache = {}

    nodes, edges = {}, []
    abs_index = {}  # normcase abs path -> node id
    proj_of = {}
    facts = {}      # node id -> parsed facts
    docs = {}       # project -> claude.md summary
    declared = {}   # project -> names listed under "## Related projects" in its CLAUDE.md

    for pid, p in projects.items():
        nodes[pid] = {"id": pid, "type": "project", "label": pid.split("/")[-1],
                      "project": pid, "archived": p["archived"], "summary": "",
                      "files": 0, "path": str(p["path"])}

    by_project = defaultdict(list)
    for f in files:
        by_project[f["project"]].append(f)

    for pid in projects:
        flist = by_project.get(pid, [])
        n_cached = 0
        for f in flist:
            ap = f["abs"]
            ext = ap.suffix.lower()
            name = ap.name
            rel = ap.relative_to(projects[pid]["path"]).as_posix()
            fid = f"{pid}/{rel}"
            if name == "CLAUDE.md" and len(ap.relative_to(projects[pid]["path"]).parts) == 1:
                txt = read_text(ap, cfg["max_text_bytes"]) or ""
                docs[pid] = summarize_md(txt)
                declared[pid] = parse_related(txt)
                continue
            if name in ("CLAUDE.md", "appsscript.json"):
                continue
            if name == ".clasp.json":
                nodes[fid] = {"id": fid, "type": "file", "label": name, "project": pid, "path": rel,
                              "lang": "clasp", "archived": f["archived"], "size": 0}
                abs_index[norm(ap)] = fid
                proj_of[fid] = pid
                continue
            try:
                st = ap.stat()
            except OSError:
                continue
            nodes[fid] = {"id": fid, "type": "file", "label": name, "project": pid, "path": rel,
                          "lang": LANG.get(ext, "other"), "archived": f["archived"],
                          "size": st.st_size, "mtime": int(st.st_mtime)}
            abs_index[norm(ap)] = fid
            proj_of[fid] = pid
            key = f"v2|{fid}|{int(st.st_mtime)}|{st.st_size}|{int(f['archived'])}"
            if key in cache:
                facts[fid] = cache[key]
                new_cache[key] = cache[key]
                n_cached += 1
                continue
            txt = read_text(ap, cfg["max_text_bytes"])
            fact = {"hosts": [], "systems": [], "env": [], "imports": [], "sys_path": [],
                    "unresolved_sys_path": 0, "symbols": [], "strings": [], "js_imports": [],
                    "script_refs": [], "text_lines": 0}
            if txt is not None:
                fact["text_lines"] = txt.count("\n") + 1
                fact["hosts"] = sorted(set(m.group(1).lower() for m in URL_RE.finditer(txt)))
                fact["systems"] = [s for s, rx in SYSTEM_PATTERNS if rx.search(txt)]
                fact["env"] = sorted(set(ENV_PY_RE.findall(txt)) | set(ENV_JS_RE.findall(txt)))
                if ext == ".py":
                    fact.update(parse_python(txt, ap, with_symbols=not f["archived"]))
                elif LANG.get(ext) in ("js", "ts", "appsscript"):
                    fact["js_imports"] = [{"spec": m.group(1), "line": txt.count("\n", 0, m.start()) + 1}
                                          for m in JS_IMPORT_RE.finditer(txt)]
                elif LANG.get(ext) == "shell":
                    refs = []
                    for ln, line in enumerate(txt.splitlines(), 1):
                        if line.lstrip().lower().startswith(("rem ", "::", "#")):
                            continue
                        for m in SCRIPT_REF_RE.finditer(line):
                            refs.append({"ref": m.group(1).strip(), "line": ln})
                    fact["script_refs"] = refs[:40]
                fact["strings"] = fact.get("strings", [])[:300]
            facts[fid] = fact
            new_cache[key] = fact
        log(f"  scanned {pid}: {len(flist)} files ({n_cached} cached)")

    # project file counts
    for fid, n in nodes.items():
        if n["type"] == "file":
            nodes[n["project"]]["files"] += 1
    for pid, s in docs.items():
        if pid in nodes:
            nodes[pid]["summary"] = s

    # ---- edges
    pname_to_pid = {}
    for pid in projects:
        pname_to_pid.setdefault(pid.split("/")[-1], []).append(pid)
    edge_seen = set()

    def add_edge(a, b, kind, evidence="", conf="exact"):
        if a == b:
            return
        k = (a, b, kind)
        if k in edge_seen:
            return
        edge_seen.add(k)
        edges.append({"from": a, "to": b, "kind": kind, "evidence": evidence, "confidence": conf})

    def lookup(p):
        return abs_index.get(norm(p))

    def resolve_mod(base_dir, mod, names):
        """Return list of file abs paths for 'import mod' / 'from mod import names'."""
        parts = mod.split(".") if mod else []
        d = os.path.join(base_dir, *parts) if parts else base_dir
        hits = []
        if parts:
            for cand in (d + ".py", os.path.join(d, "__init__.py")):
                if lookup(cand):
                    hits.append(cand)
                    break
        for n in names:
            for cand in (os.path.join(d, n + ".py"), os.path.join(d, n, "__init__.py")):
                if lookup(cand):
                    hits.append(cand)
                    break
        return hits

    sys_unresolved = defaultdict(int)
    name_dirs_cache = {}
    for fid, fact in facts.items():
        node = nodes[fid]
        pid = node["project"]
        ap = Path(projects[pid]["path"]) / node["path"]
        fdir = str(ap.parent)
        proot = str(projects[pid]["path"])
        sys_unresolved[pid] += fact.get("unresolved_sys_path", 0)

        # python imports
        for imp in fact.get("imports", []):
            ev = f"{node['path']}:{imp['line']}"
            found = None
            if imp["level"] > 0:
                base = fdir
                for _ in range(imp["level"] - 1):
                    base = os.path.dirname(base)
                found = [(h, "exact") for h in resolve_mod(base, imp["mod"], imp["names"])]
            else:
                found = [(h, "exact") for h in resolve_mod(fdir, imp["mod"], imp["names"])]
                if not found:
                    for sp in fact.get("sys_path", []):
                        found = [(h, "resolved") for h in resolve_mod(sp["dir"], imp["mod"], imp["names"])]
                        if found:
                            break
                if not found:
                    cur = os.path.dirname(fdir)
                    while norm(cur).startswith(norm(proot)) and not found:
                        found = [(h, "heuristic") for h in resolve_mod(cur, imp["mod"], imp["names"])]
                        if norm(cur) == norm(proot):
                            break
                        cur = os.path.dirname(cur)
                    if not found:
                        found = [(h, "heuristic") for h in resolve_mod(proot, imp["mod"], imp["names"])]
            for h, conf in found:
                add_edge(fid, lookup(h), "imports", ev, conf)

        # js imports
        for imp in fact.get("js_imports", []):
            spec = imp["spec"]
            ev = f"{node['path']}:{imp['line']}"
            bases = []
            if spec.startswith("."):
                bases.append((os.path.normpath(os.path.join(fdir, spec)), "exact"))
            elif spec.startswith("@/") or spec.startswith("~/"):
                for sub in ("src", ""):
                    bases.append((os.path.normpath(os.path.join(proot, sub, spec[2:])), "heuristic"))
            for b, conf in bases:
                cands = [b] + [b + e for e in JS_EXT] + [os.path.join(b, "index" + e) for e in JS_EXT]
                for c in cands:
                    if lookup(c):
                        add_edge(fid, lookup(c), "imports", ev, conf)
                        break

        # shell: runs scripts
        for r in fact.get("script_refs", []):
            ref = r["ref"].replace("%~dp0", fdir + os.sep).replace("$PSScriptRoot", fdir)
            ev = f"{node['path']}:{r['line']}"
            cands = []
            if re.match(r"^[A-Za-z]:[\\/]", ref):
                cands.append((ref, "exact"))
            else:
                cands.append((os.path.join(fdir, ref), "exact"))
                cands.append((os.path.join(proot, ref), "resolved"))
            for c, conf in cands:
                if lookup(c):
                    add_edge(fid, lookup(c), "runs", ev, conf)
                    break

        # targets: hosts, systems, env
        ignore = set(cfg["ignore_hosts"])
        for h in fact.get("hosts", []):
            if h in ignore:
                continue
            label = cfg["host_aliases"].get(h, h)
            tid = f"target:{label}"
            if tid not in nodes:
                nodes[tid] = {"id": tid, "type": "target", "label": label, "tkind": "host", "project": ""}
            add_edge(fid, tid, "uses_target", node["path"], "exact")
        for s in fact.get("systems", []):
            tid = f"target:{s}"
            if tid not in nodes:
                nodes[tid] = {"id": tid, "type": "target", "label": s.split(":", 1)[1], "tkind": "system", "project": ""}
            add_edge(fid, tid, "uses_target", node["path"], "heuristic")
        for e in fact.get("env", []):
            tid = f"env:{e}"
            if tid not in nodes:
                nodes[tid] = {"id": tid, "type": "target", "label": e, "tkind": "env", "project": ""}
            add_edge(fid, tid, "uses_target", node["path"], "exact")

    # declared dependencies (project CLAUDE.md "## Related projects")
    for pid, names in declared.items():
        for nm in names:
            for tp in list(pname_to_pid.get(nm, [])) + ([nm] if nm in projects else []):
                if tp != pid:
                    add_edge(pid, tp, "declared", "CLAUDE.md", "exact")

    # function nodes and call edges (python, non-archived)
    file_funcs = {}
    for fid, fact in facts.items():
        fns = fact.get("funcs") or {}
        if not fns:
            continue
        file_funcs[fid] = set(fns)
        for name, info in fns.items():
            nodes[f"{fid}::{name}"] = {"id": f"{fid}::{name}", "type": "function", "label": name,
                                       "project": nodes[fid]["project"], "file": fid, "line": info["line"]}

    def alias_target(fact, fdir, proot, alias, symbol):
        """Return (target file id, confidence) for alias.symbol, or None."""
        bases = []
        if alias["level"] > 0:
            b = fdir
            for _ in range(alias["level"] - 1):
                b = os.path.dirname(b)
            bases.append((b, "exact"))
        else:
            bases.append((fdir, "exact"))
            bases += [(sp["dir"], "resolved") for sp in fact.get("sys_path", [])]
            cur = os.path.dirname(fdir)
            while norm(cur).startswith(norm(proot)):
                bases.append((cur, "heuristic"))
                if norm(cur) == norm(proot):
                    break
                cur = os.path.dirname(cur)
        for b, conf in bases:
            if symbol is None:                      # alias is a module: import m / from x import m
                names = [alias["name"]] if alias["name"] else []
                hits = resolve_mod(b, alias["mod"], names)
                hits = hits[-1:] if names else hits
            else:                                   # from m import symbol
                hits = resolve_mod(b, alias["mod"], []) if alias["mod"] else []
            if hits:
                tf = lookup(hits[0])
                if tf:
                    return tf, conf
        return None

    for fid, fact in facts.items():
        if fid not in file_funcs and not fact.get("module_calls"):
            continue
        ap = Path(projects[nodes[fid]["project"]]["path"]) / nodes[fid]["path"]
        fdir, proot = str(ap.parent), str(projects[nodes[fid]["project"]]["path"])
        aliases = fact.get("aliases", {})
        callers = [(f"{fid}::{n}", i["calls"]) for n, i in (fact.get("funcs") or {}).items()]
        callers.append((fid, fact.get("module_calls", [])))
        for caller, calls in callers:
            for kind, a, b in calls:
                target, conf = None, "exact"
                if kind == "n":
                    if a in file_funcs.get(fid, ()):
                        target = f"{fid}::{a}"
                    elif a in aliases and aliases[a]["name"]:
                        r = alias_target(fact, fdir, proot, aliases[a], aliases[a]["name"])
                        if r and aliases[a]["name"] in file_funcs.get(r[0], ()):
                            target, conf = f"{r[0]}::{aliases[a]['name']}", r[1]
                elif a in aliases:
                    r = alias_target(fact, fdir, proot, aliases[a], None)
                    if r and b in file_funcs.get(r[0], ()):
                        target, conf = f"{r[0]}::{b}", r[1]
                if target and target != caller:
                    add_edge(caller, target, "calls", nodes[fid]["path"], conf)

    # cross-project path references (no import needed)
    names = [n for n in pname_to_pid if len(n) >= 4]
    if names:
        alt = "|".join(re.escape(n) for n in sorted(names, key=len, reverse=True))
        path_re = re.compile(r"(?:[\\/])(" + alt + r")(?:[\\/])([^\"'\s\\/][^\"'\r\n]*)?")
        for fid, node in list(nodes.items()):
            if node["type"] != "file":
                continue
            ap = Path(projects[node["project"]]["path"]) / node["path"]
            if node["lang"] not in ("python", "js", "ts", "appsscript", "shell"):
                continue
            txt = read_text(ap, cfg["max_text_bytes"])
            if not txt:
                continue
            for ln, line in enumerate(txt.splitlines(), 1):
                for m in path_re.finditer(line):
                    for target_pid in pname_to_pid[m.group(1)]:
                        if target_pid == node["project"]:
                            continue
                        ev = f"{node['path']}:{ln}"
                        tail = (m.group(2) or "").replace("\\", "/")
                        tfile = None
                        cand = os.path.join(projects[target_pid]["path"], tail)
                        if tail and lookup(cand):
                            tfile = lookup(cand)
                        is_comment = line.lstrip().startswith(("#", "//", "REM ", "rem ", "::", "*"))
                        add_edge(fid, tfile or target_pid, "reads_file", ev, "doc" if is_comment else "heuristic")
            # exact-name string constants (Path(...) / "Project Name")
        for fid, fact in facts.items():
            node = nodes[fid]
            for sv in fact.get("strings", []):
                if sv in pname_to_pid:
                    for target_pid in pname_to_pid[sv]:
                        if target_pid != node["project"] and len(sv) >= 5:
                            add_edge(fid, target_pid, "reads_file", node["path"], "heuristic")

    # scheduled jobs
    if cfg.get("schedule"):
        add_scheduled(nodes, add_edge, lookup, projects)

    # ---- aggregate project edges
    pe = defaultdict(lambda: {"count": 0, "kinds": set()})
    for e in edges:
        a, b = nodes.get(e["from"]), nodes.get(e["to"])
        if not a or not b:
            continue
        pa = a["project"]
        pb = b["project"] if b["type"] != "target" else b["id"]
        if a["type"] == "project":
            pa = a["id"]
        if b["type"] == "project":
            pb = b["id"]
        if pa and pb and pa != pb:
            pe[(pa, pb)]["count"] += 1
            pe[(pa, pb)]["kinds"].add(e["kind"])
    project_edges = [{"from": a, "to": b, "count": v["count"], "kinds": sorted(v["kinds"])}
                     for (a, b), v in sorted(pe.items())]

    hygiene = build_hygiene(projects, nodes, sys_unresolved)
    meta = {
        "generated": time.strftime("%Y-%m-%d %H:%M:%S"),
        "projects": len(projects),
        "files": sum(1 for n in nodes.values() if n["type"] == "file"),
        "functions": sum(1 for n in nodes.values() if n["type"] == "function"),
        "targets": sum(1 for n in nodes.values() if n["type"] == "target"),
        "edges": len(edges),
        "include_archive": cfg["include_archive"],
        "note": "Static scan. Edges tagged exact / resolved / heuristic. Missing edge does not prove independence.",
    }
    graph = {"meta": meta, "nodes": list(nodes.values()), "edges": edges,
             "project_edges": project_edges, "hygiene": hygiene}
    try:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(new_cache), encoding="utf-8")
    except OSError:
        pass
    log(f"scan done in {time.time() - t0:.1f}s")
    return graph


def parse_related(txt):
    """Backticked names under a "## Related projects" heading (a project's declared needs)."""
    m = re.search(r"^##\s+Related projects\s*$(.*?)(?=^##\s|\Z)", txt, re.S | re.M)
    return sorted(set(re.findall(r"`([^`\n]+)`", m.group(1)))) if m else []


def summarize_md(txt):
    for para in re.split(r"\n\s*\n", txt):
        p = para.strip()
        if p and not p.startswith("#") and not p.startswith("|") and not p.startswith("```"):
            p = re.sub(r"\s+", " ", p)
            return p[:300]
    return ""


def add_scheduled(nodes, add_edge, lookup, projects):
    if os.name != "nt":
        log("schedule: skipped (not Windows)")
        return
    try:
        res = subprocess.run(["schtasks", "/query", "/fo", "csv", "/v"], capture_output=True,
                             text=True, timeout=60, encoding="utf-8", errors="replace")
    except (OSError, subprocess.SubprocessError) as e:
        log(f"schedule: schtasks failed ({e})")
        return
    import csv
    import io
    rows = list(csv.reader(io.StringIO(res.stdout)))
    if not rows:
        return
    hdr = rows[0]
    try:
        i_name, i_run = hdr.index("TaskName"), hdr.index("Task To Run")
    except ValueError:
        log("schedule: unexpected schtasks columns, skipped")
        return
    i_sched = hdr.index("Schedule Type") if "Schedule Type" in hdr else None
    seen = set()
    count = 0
    for r in rows[1:]:
        if len(r) <= max(i_name, i_run):
            continue
        name, cmd = r[i_name], r[i_run]
        for fid_abs in re.findall(r"[A-Za-z]:\\[^\"]*?\.(?:py|js|ps1|bat|cmd)", cmd):
            fid = lookup(fid_abs)
            if fid and (name, fid) not in seen:
                seen.add((name, fid))
                jid = f"job:{name.strip(chr(92))}"
                if jid not in nodes:
                    nodes[jid] = {"id": jid, "type": "job", "label": name.strip("\\"), "project": "",
                                  "schedule": r[i_sched] if i_sched is not None and len(r) > i_sched else ""}
                add_edge(jid, fid, "runs", "Task Scheduler", "exact")
                count += 1
    log(f"schedule: {count} job->script links")


def build_hygiene(projects, nodes, sys_unresolved):
    out = []
    for pid, p in projects.items():
        path = p["path"]
        issues = []
        if not (path / "CLAUDE.md").exists():
            issues.append("no CLAUDE.md")
        if not (path / "architecture.md").exists():
            issues.append("no architecture.md")
        if not (path / "Handover").is_dir():
            issues.append("no Handover/")
        for legacy in ("Scripts", "Data"):
            if (path / legacy).is_dir():
                issues.append(f"legacy {legacy}/ folder")
        if sys_unresolved.get(pid):
            issues.append(f"{sys_unresolved[pid]} unresolved sys.path call(s)")
        out.append({"project": pid, "issues": issues})
    return out


# ---------------------------------------------------------------- impact
def build_impact(graph):
    nodes = {n["id"]: n for n in graph["nodes"]}
    rev = defaultdict(set)   # dependency -> dependents
    targets = defaultdict(set)
    jobs_of = defaultdict(set)
    for e in graph["edges"]:
        if e["kind"] in ("imports", "runs", "reads_file", "calls", "declared"):
            if nodes.get(e["from"], {}).get("type") == "job":
                jobs_of[e["to"]].add(e["from"])
            else:
                rev[e["to"]].add(e["from"])
        elif e["kind"] == "uses_target":
            targets[e["from"]].add(e["to"])
    impact = {}
    for nid, n in nodes.items():
        if n["type"] not in ("file", "project", "function"):
            continue
        direct = sorted(rev.get(nid, ()))
        seen, dq = set(), deque(direct)
        while dq:
            cur = dq.popleft()
            if cur in seen:
                continue
            seen.add(cur)
            dq.extend(rev.get(cur, ()))
        trans = sorted(seen)
        file_dep = [x for x in trans if nodes.get(x, {}).get("type") in ("file", "function")]
        projs = sorted({nodes[x]["project"] for x in trans if x in nodes and nodes[x]["project"]} - {n["project"]})
        jobs = set()
        for x in [nid] + trans:
            jobs |= jobs_of.get(x, set())
        if not direct and not jobs and not targets.get(nid):
            continue
        impact[nid] = {"direct": direct, "transitive": file_dep, "projects": projs,
                       "jobs": sorted(jobs), "targets": sorted(targets.get(nid, ()))}
    return impact


# ---------------------------------------------------------------- output
def write_index(graph, impact, path):
    nodes = {n["id"]: n for n in graph["nodes"]}
    hyg = {h["project"]: h["issues"] for h in graph["hygiene"]}
    uses = defaultdict(set)
    dep_on = defaultdict(set)
    depended = defaultdict(set)
    for e in graph["edges"]:
        a, b = nodes.get(e["from"]), nodes.get(e["to"])
        if not a or not b:
            continue
        if b["type"] == "target":
            uses[a["project"]].add(b["label"])
        elif a["project"] and b["project"] and a["project"] != b["project"] and a["type"] != "job":
            dep_on[a["project"]].add(b["project"])
            depended[b["project"]].add(a["project"])
    lines = ["# Project index", "",
             f"Generated {graph['meta']['generated']}. Static scan: edges are exact / resolved / heuristic. "
             "Run `python scanner/impact.py <file>` before editing a shared file.", ""]
    for n in sorted((x for x in graph["nodes"] if x["type"] == "project"), key=lambda x: x["id"].lower()):
        pid = n["id"]
        lines.append(f"## {pid}" + (" (archived)" if n.get("archived") else ""))
        if n.get("summary"):
            lines.append(n["summary"])
        lines.append(f"- files scanned: {n['files']}")
        if dep_on[pid]:
            lines.append("- depends on: " + ", ".join(sorted(dep_on[pid])))
        if depended[pid]:
            lines.append("- depended on by: " + ", ".join(sorted(depended[pid])))
        tg = sorted(t for t in uses[pid] if not t.isupper())
        if tg:
            lines.append("- targets: " + ", ".join(tg[:25]) + (" ..." if len(tg) > 25 else ""))
        if hyg.get(pid):
            lines.append("- hygiene: " + "; ".join(hyg[pid]))
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def self_check(*texts):
    hits = []
    for t in texts:
        for rx in SECRET_PATTERNS:
            for m in rx.finditer(t):
                hits.append(m.group(0)[:12] + "...")
    return hits


def first_run_setup(local_cfg):
    """No config yet: ask which folder holds the projects and save scanner/config.local.json."""
    default = HERE.parent.parent
    print("First run: which folder holds your projects? Each subfolder of it is one project.", flush=True)
    print(f"Press Enter to use {default}", flush=True)
    answer = input("Projects folder: ").strip().strip('"')
    root = Path(answer or default).expanduser().resolve()
    if not root.is_dir():
        log(f"Not a folder: {root}. Nothing saved.")
        sys.exit(1)
    cfg = {"root": root.as_posix(), "exclude_projects": [HERE.parent.name]}
    local_cfg.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
    log(f"saved {local_cfg} (edit it later to change the folder or exclude projects)")


def main():
    ap = argparse.ArgumentParser(description="Agentic_View scanner")
    ap.add_argument("--config", help="path to a JSON config (default: config.local.json if present)")
    ap.add_argument("--root", help="folder whose subfolders are projects (overrides config)")
    ap.add_argument("--archive", action="store_true", help="include Archive folders")
    ap.add_argument("--schedule", action="store_true", help="read Windows Task Scheduler (read-only)")
    ap.add_argument("--no-cache", action="store_true")
    args = ap.parse_args()

    local_cfg = HERE / "config.local.json"
    if not args.config and not args.root and not local_cfg.exists() and sys.stdin.isatty():
        first_run_setup(local_cfg)
    cfg_path = args.config or (str(local_cfg) if local_cfg.exists() else None)
    cfg = load_config(cfg_path)
    if args.root:
        cfg["root"] = args.root
    if args.archive:
        cfg["include_archive"] = True
    if args.schedule:
        cfg["schedule"] = True
    roots = [{"path": cfg["root"]}] + [r for r in cfg.get("extra_roots", [])]
    log(f"config: {cfg_path or 'defaults'}; root: {Path(cfg['root']).resolve()}")

    graph = scan(cfg, roots, use_cache=not args.no_cache)
    if not graph["meta"]["projects"]:
        log(f"No projects found in {Path(cfg['root']).resolve()}. 'root' must be a folder whose "
            f"subfolders are your projects. Edit scanner/config.local.json (use forward slashes, "
            f"e.g. \"C:/Users/you/Projects\") or run with --root <folder>.")
        sys.exit(1)
    impact = build_impact(graph)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    g_txt = json.dumps(graph, ensure_ascii=True, indent=1)
    i_txt = json.dumps(impact, ensure_ascii=True)
    hits = self_check(g_txt, i_txt)
    if hits:
        log(f"FAIL: secret-like strings in output ({len(hits)}): {hits[:5]}. Nothing written.")
        sys.exit(2)
    (OUT_DIR / "graph.json").write_text(g_txt, encoding="utf-8")
    (OUT_DIR / "impact.json").write_text(i_txt, encoding="utf-8")
    (OUT_DIR / "graph.js").write_text(
        "window.AGENTIC_GRAPH=" + json.dumps(graph, ensure_ascii=True) +
        ";window.AGENTIC_IMPACT=" + i_txt + ";", encoding="utf-8")
    write_index(graph, impact, OUT_DIR / "INDEX.md")
    m = graph["meta"]
    log(f"wrote out/: {m['projects']} projects, {m['files']} files, {m['targets']} targets, "
        f"{m['edges']} edges, {len(impact)} impact entries; secret self-check passed")


if __name__ == "__main__":
    main()
