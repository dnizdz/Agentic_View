# Agentic View - agent rules (template)

Copy this file to `CLAUDE.md` (gitignored) and adapt it to your own setup.

| Task | Read, in order | Not needed |
|---|---|---|
| Rescan the projects | `scanner/scan.py --help`, `scanner/config.local.json` | `viewer/` |
| Ask "what breaks if I change X" | run `python scanner/impact.py <file>` | `scanner/scan.py` |
| Change the map UI | `viewer/index.html` | `scanner/`, `server/` |
| Change chat, scripts, files or setup behaviour | `server/server.py`, then the matching tab in `viewer/index.html` | `scanner/` |

## Rules
- Before editing a file in a scanned project, run `python scanner/impact.py <file>` and review dependents.
- Heuristic edges can be wrong and a missing edge is not proof of independence. Say so when reporting impact.
- Never read or print `.env` values. The scanner stores env key names only.
- `out/` and `scanner/config.local.json` are local data. Never commit or publish them.
- Keep `architecture.md` current when a module boundary or external call changes.
- The server (`server/server.py`) is a browser-to-shell bridge. Keep it bound to 127.0.0.1, keep the token and Host checks, and never accept raw paths or commands from the page.
