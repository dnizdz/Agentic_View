# Agentic View

Scan a folder of projects and see how they connect. Agentic View builds a dependency map (imports, scripts that run other scripts, projects that read each other's files, shared external systems) and gives you:

- an interactive map you open in a browser (project to file drill-down, impact highlighting),
- `impact.py`, a command that answers "what depends on this file?" for you or for an AI agent,
- `out/graph.json`, `out/impact.json` and `out/INDEX.md`, a machine-readable map and a short per-project summary that agents can grep.

Read-only: it never modifies the projects it scans and never opens `.env` files (env key names found in code are recorded, values are not).

## Requirements
Python 3.9+ (standard library only) and a modern browser.

## Quick start
```
python scanner/scan.py --root /path/to/folder/of/projects
python scanner/impact.py path/of/some_file.py
```
Then open `viewer/index.html` in a browser.

For a persistent setup, copy `scanner/config.example.json` to `scanner/config.local.json` (gitignored) and run `python scanner/scan.py`.

Options: `--archive` includes `Archive/` folders, `--schedule` reads Windows Task Scheduler (read-only) to mark scheduled scripts, `--no-cache` forces a full rescan.

## What it detects
- Python imports, including `sys.path.insert(...)` with `Path(__file__).parents[n]` and similar patterns
- JS/TS imports and `require`, Apps Script files, `.bat` / `.ps1` scripts that run other scripts
- Paths that point into another project's folder (dependencies without an import)
- External targets: URL hostnames, Odoo/XML-RPC, Google Sheets/Drive/Gmail, env key names

Every edge is tagged `exact`, `resolved`, `heuristic` or `doc` (found only in a comment). Static analysis cannot see everything, so a missing edge is not proof of independence.

## Privacy
Scan output contains your project names, hostnames and paths. `out/` and `scanner/config.local.json` are gitignored. Do not publish them.

## Status
Phase 1: project and file level map. Planned: function level, 3D view, one-click "open Claude here" launcher.

## License
MIT
