# Changelog

Versions follow semantic versioning. The current version is in the `VERSION` file and is shown in the page header. Each release is a git tag `vX.Y.Z`.

## 1.2.0 - 2026-10-27
### Added
- Update button (header): checks GitHub for the newest tagged version and updates the app by downloading the release zip. No git needed, works the same on Windows, macOS and Ubuntu. Replaces app files only; your `scanner/config.local.json`, `out/`, projects, `CLAUDE.md` and handover notes are never touched, and the replaced files are saved under `out/backups/update-<version>-<time>/`. A git clone is told to use `git pull` instead. Blocked in `--read-only`. After an update, stop the server and run the launcher again.
- The header shows "Update available" when a newer version exists (one request to GitHub when the page loads).

## 1.1.0 - 2026-10-27
### Added
- Files tab: open a file with its default app, or show it in Explorer/Finder. Folder rows and the project folder can be shown too. Files that would run when opened (scripts, executables) are refused for "default app".
- Files tab: files grouped in a collapsible folder tree with file counts.
- Files tab: drag files from Explorer/Finder onto a folder (or the empty area for the project root) to copy them in. Confirms first, never overwrites silently, backs up a replaced file, 25 MB per file, folders are skipped.
- Chat: a "Latest" button jumps to the newest message. The chat no longer pulls you down while you read older messages.
- Full-width project view: clicking a project gives the whole window to the right panel, with a project switcher and a "Back to map" button.
- "Scheduler Job" tab per project: lists Windows Task Scheduler jobs that run a file of the project (schedule, next and last run, result, status) with Run now, Enable and Disable.
- System-wide Scheduler jobs: header button with the full list, a "Scheduler jobs only" filter for the board, map and project list, and a jobs section in each board column.
- Launchers per OS: `start_windows.bat`, `start_mac.command`, `start_ubuntu.sh` (they replace `start.bat` and `start.sh`).
- `--no-schedule` scanner flag.

### Changed
- The scanner reads Windows Task Scheduler by default (read-only, skipped on non-Windows).
- `.gitattributes` keeps `.sh` and `.command` files on LF line endings so they run on macOS and Linux.

## 1.0.0 - 2026-10-27
- First tagged state: scanner (files, Python functions, cross-project links, external systems), impact tool, Board, 2D and 3D views, local server with live Claude chat per project, Files editor, Scripts runner, New project, Global CLAUDE.md editor.
