# Agentic View

Scan a folder of projects and see how they connect. Agentic View builds a dependency map (imports, function calls, scripts that run other scripts, projects that read each other's files, shared external systems) and lets you manage those projects from one page.

- **Map:** interactive 2D graph (project, then file, then function) and a 3D view, with impact highlighting.
- **For agents:** `impact.py` answers "what depends on this file?", plus `out/graph.json`, `out/impact.json` and `out/INDEX.md`.
- **Manage (optional local server):** stream a Claude chat per project, run and stop scanned scripts, view and edit project files, rescan, and set up Claude (install and sign in) from the page.

The scanner is read-only and never opens `.env` files (env key names found in code are recorded, values are not).

## Requirements
Python 3.9+ (standard library only) and a modern browser. The page loads Cytoscape.js, 3d-force-graph and fonts from public CDNs. Chat needs Claude Code (the Setup screen can install it).

## Quick start
```
python scanner/scan.py --root /path/to/folder/of/projects
python scanner/impact.py path/of/some_file.py
```
Open `viewer/index.html` for the map only, or start the server for everything:
```
python server/server.py --open
```
One-step launcher (rescans, starts the server, opens the browser): double-click `start.bat` on Windows, or run `./start.sh` on macOS and Linux. Extra flags pass through, e.g. `start.bat --read-only`.

For a persistent setup, copy `scanner/config.example.json` to `scanner/config.local.json` (gitignored).

Scanner options: `--archive` includes `Archive/` folders, `--schedule` reads Windows Task Scheduler (read-only), `--no-cache` forces a full rescan.

## The local server
`server/server.py` is a browser-to-shell bridge, so it is strict:

- Binds `127.0.0.1` only. Host and Origin headers are checked. A random token is required on every API call.
- The page sends project and file ids. The server maps them to folders from your scan. No raw paths, commands or arguments come from the page.
- File edits stay inside the project folder, refuse secret files (`.env`, keys, tokens), and keep the previous version under `out/backups/`.
- Scripts run only if they appear in the scan, with no arguments, after a confirm dialog.
- Chat runs `claude -p --output-format stream-json` in the project folder. The permission mode follows that project's own Claude settings by default. You can switch to accept-edits or plan-only. "Bypass ALL permissions" (`--dangerously-skip-permissions`) only works if you start the server with `--allow-bypass`, and asks for confirmation.
- The Setup screen shows the exact official install command and runs it only after you confirm. Sign-in is the normal Claude browser login, streamed into the page.
- `--read-only` blocks file edits, script runs, install and any chat except plan mode.
- You start it and you stop it (Ctrl+C). It is not a service. Do not expose the port to a network.

## What the scanner detects
- Python imports (including `sys.path.insert(...)` with `Path(__file__).parents[n]` patterns) and function-to-function calls across files
- JS/TS imports and `require`, Apps Script files, `.bat` / `.ps1` scripts that run other scripts
- Paths that point into another project's folder (dependencies without an import)
- External targets: URL hostnames, Odoo/XML-RPC, Google Sheets/Drive/Gmail, env key names

Every edge is tagged `exact`, `resolved`, `heuristic` or `doc` (found only in a comment). Static analysis cannot see everything, so a missing edge is not proof of independence. Function-level detail covers Python only; class methods are not separate nodes yet.

## Privacy
Scan output contains your project names, hostnames and paths. `out/` and `scanner/config.local.json` are gitignored. Do not publish them.

## License
MIT
