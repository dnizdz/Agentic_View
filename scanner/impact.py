#!/usr/bin/env python3
"""Answer "what breaks if I change this?" from out/impact.json. Standard library only.

Usage:
  python scanner/impact.py <file-or-fragment>     files, projects, jobs, targets affected
  python scanner/impact.py --target <fragment>    everything touching a target (host, system, env key)
  python scanner/impact.py <x> --json             machine-readable
"""
import argparse
import json
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "out"


def load():
    try:
        graph = json.loads((OUT / "graph.json").read_text(encoding="utf-8"))
        impact = json.loads((OUT / "impact.json").read_text(encoding="utf-8"))
    except OSError:
        print("No scan output. Run: python scanner/scan.py", file=sys.stderr)
        sys.exit(1)
    return graph, impact


def match(ids, q):
    q = q.replace("\\", "/").lower()
    exact = [i for i in ids if i.lower() == q]
    if exact:
        return exact
    return [i for i in ids if q in i.lower()]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("query")
    ap.add_argument("--target", action="store_true")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    graph, impact = load()
    nodes = {n["id"]: n for n in graph["nodes"]}

    if a.target:
        tids = match([i for i, n in nodes.items() if n["type"] == "target"], a.query)
        res = {}
        for e in graph["edges"]:
            if e["kind"] == "uses_target" and e["to"] in tids:
                res.setdefault(e["to"], []).append(e["from"])
        if a.json:
            print(json.dumps(res, indent=1))
            return
        if not res:
            print("no matching target")
        for t, fs in res.items():
            print(f"{t}  ({len({nodes[f]['project'] for f in fs})} projects, {len(fs)} files)")
            for f in sorted(fs):
                print(f"  {f}")
        return

    ids = match([i for i, n in nodes.items() if n["type"] in ("file", "project")], a.query)
    if not ids:
        print("no matching file or project")
        sys.exit(1)
    if len(ids) > 1 and not a.json:
        print(f"{len(ids)} matches, showing each:")
    out = {i: impact.get(i, {"direct": [], "transitive": [], "projects": [], "jobs": [], "targets": []}) for i in ids[:15]}
    if a.json:
        print(json.dumps(out, indent=1))
        return
    for i, r in out.items():
        print(f"\n== {i}")
        print(f"direct dependents ({len(r['direct'])}):")
        for x in r["direct"]:
            print(f"  {x}")
        extra = [x for x in r["transitive"] if x not in r["direct"]]
        print(f"transitive ({len(extra)} more):")
        for x in extra[:40]:
            print(f"  {x}")
        print("affected projects: " + (", ".join(r["projects"]) or "none"))
        print("scheduled jobs: " + (", ".join(r["jobs"]) or "none"))
        print("targets touched: " + (", ".join(r["targets"]) or "none"))
    print("\nNote: static scan. Edges tagged heuristic can be false; absence of an edge is not proof of independence.")


if __name__ == "__main__":
    main()
