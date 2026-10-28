# Changelog

Versions follow semantic versioning. The current version is in the `VERSION` file and is shown in the page header. Each release is a git tag `vX.Y.Z`.

## 1.8.0 - 2026-10-28
### Added
- The scanner now reads each project's root `architecture.md` (and the prose of its `CLAUDE.md`) for names of other projects. A match adds a `mentions` edge tagged `doc`: shown on the map and board by default as a dashed grey-blue line, labelled "mentioned in docs". Names need 5+ characters and an exact, case-sensitive match. Mentions are not counted in impact lists. `## Related projects` in `CLAUDE.md` still gives the firm `declared` edge.

## 1.7.0 - 2026-10-28
### Added
- Live tool approval in the chat. When Claude wants a tool your mode does not already allow, the chat shows a card with the tool and its input: Approve, Approve for this session (session-only rule, nothing written to settings) or Deny. Cards survive a page reload and expire when the session ends. Applies to "Follow project settings" and "Accept edits"; plan and bypass are unchanged. Approving is refused in `--read-only`.
- Subagent activity is nested under its Agent call with a status tag (starting, running, completed). Subagent partial text no longer mixes into the main reply.

## 1.6.0 - 2026-10-28
### Added
- Chat tab has a Model picker (default, Fable 5.1, Opus 5.5, Sonnet 5.5, Haiku 5.5), remembered per project. A change restarts the live session on the next message and resumes it. The live session reports its model so a reloaded page shows the right choice.

## 1.5.6 - 2026-10-28
### Added
- `create_shortcut.py` makes a Desktop shortcut for the current OS (Windows `.lnk`, macOS `.command`, Linux `.desktop`); `--dir` picks another folder, `--remove` deletes it. Step 7 of `INSTALL.md`.
### Fixed
- The in-app Update now also replaces `INSTALL.md` and `create_shortcut.py`.

## 1.5.5 - 2026-10-28
### Added
- `INSTALL.md`: step-by-step install for people and AI assistants (requirements, get the code, point at the projects folder, scan, start, check), with rules for an AI that installs it. Linked from the top of `README.md`.

## 1.5.4 - 2026-10-28
### Added
- Chat has a Stop button (like Ctrl+C): it interrupts Claude's current turn and keeps the session open. New endpoint `POST /api/run/<id>/interrupt` (chat sessions only).
- Files tab has a Refresh button.
### Fixed
- Files created by Claude (or a script) did not appear in the Files tab until the page was reloaded. The tab now reloads the list every time it opens and after each finished chat turn.

## 1.5.3 - 2026-10-28
### Changed
- The Update button is hidden unless a newer release exists. The version is checked on every page open.

## 1.5.2 - 2026-10-28
### Changed
- "Back to map" moved to the top left of the project tab bar and is now blue. Tab-bar buttons and the project switcher share one height.
- Header buttons share one size (they shrink evenly on narrow windows) and the Board / 2D / 3D toggle keeps its full width. Hidden header buttons stay hidden.
- The update button reads "Update vX.Y.Z" when a newer version exists.

## 1.5.1 - 2026-10-27
### Changed
- README: screenshots of the board, 2D map and 3D map use a made-up set of eight demo projects instead of a blurred real one.

## 1.5.0 - 2026-10-27
### Added
- The launchers ask how much the app may do before it opens: 1 Read only, 2 Allow edit (default, press Enter), 3 Allow bypass (needs a typed YES). The choice is kept across update restarts. Passing `--read-only`, `--allow-edit` or `--allow-bypass` skips the question; with no terminal it starts in Allow edit.

## 1.4.0 - 2026-10-27
### Added
- Merged from the setup-friendly-defaults work: first run in a terminal asks for the projects folder and saves `scanner/config.local.json`; the scanner skips its own folder and says so when the root has no projects; chat defaults to Accept edits; `--allow-edit` lets chat run python without asking (on by default in the launchers, ignored with `--read-only`); README explains the purpose and shows a screenshot.
- Launchers print a note when Claude Code is not installed, and a clearer message when Python is missing.

## 1.3.0 - 2026-10-27
### Changed
- The Update button now finishes the whole update by itself: it downloads, replaces the app files, restarts the server and reloads the page. No stopping or relaunching by hand. Live chat sessions end at the restart and resume on the next message.
- New `launcher.py` does the scan, starts the server and starts it again after an update. The three launchers (`start_windows.bat`, `start_mac.command`, `start_ubuntu.sh`) now only check Python and call it, so they rarely change. The updater skips files whose content is unchanged.
- The server waits up to 20 seconds for its port after a restart.
- Upgrading from 1.2.0: the update to 1.3.0 still ends with a manual restart (1.2.0 has no restart step). From 1.3.0 on it is automatic.

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
