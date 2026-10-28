# Install Agentic View

This page is written for people and for AI assistants (Claude Code, ChatGPT, Gemini, Copilot or any agent with a shell). If you are an AI assistant asked to "install Agentic View", follow the steps below in order.

## Rules for an AI assistant
- Ask the user one question before step 3: which folder holds their projects (each subfolder is one project). Do not guess it.
- Do not install anything except what is listed here. Agentic View needs no pip packages.
- Start the server in the mode the user picks. Default to `--allow-edit`. Never add `--allow-bypass` unless the user asks for it in plain words.
- Do not push, publish or upload anything. The scan output (`out/`) contains the user's project names and stays on their computer.
- Report each step's real output. If a step fails, stop and show the error.

## 1. Check requirements
- Python 3.9 or newer: `python --version` (on macOS and Linux it may be `python3`).
  - Windows: `winget install Python.Python.3`, or python.org with "Add python.exe to PATH" ticked.
  - macOS: `brew install python`, or python.org.
  - Ubuntu: `sudo apt install python3 xdg-utils`.
- A modern browser.
- Claude Code is optional and only needed for the Chat tab. The Setup screen in the page can install it.

## 2. Get the code
With git:
```
git clone https://github.com/dnizdz/Agentic_View.git
cd Agentic_View
```
Without git: open https://github.com/dnizdz/Agentic_View, click **Code**, then **Download ZIP**, unzip it, and open a terminal in the unzipped folder.

## 3. Point it at the projects folder
Create `scanner/config.local.json` (replace the path; use forward slashes on Windows):
```
{
  "root": "C:/Users/you/Projects",
  "exclude_projects": ["Agentic_View"]
}
```
`root` must be a folder whose subfolders are the projects. `exclude_projects` should name this app's own folder if it sits inside `root`. The file is gitignored.

## 4. Scan
```
python scanner/scan.py
```
Expect a summary of projects, files and links. "No projects found in <path>" means `root` is wrong.

## 5. Start
```
python server/server.py --allow-edit --open
```
The browser opens on `http://127.0.0.1:8765`. Use `--read-only` instead to forbid edits and scripts. Use `--port 9000` if 8765 is taken.

Or use the launcher for your OS, which rescans, starts the server, opens the browser and asks which access level to use:

| OS | Command |
|---|---|
| Windows | `start_windows.bat` |
| macOS | `start_mac.command` |
| Ubuntu / Linux | `chmod +x start_ubuntu.sh && ./start_ubuntu.sh` |

## 6. Check it works
- The page shows the Board view with one column per project.
- Click a project: Chat, Files and Scripts tabs appear.
- If the Chat tab says Claude Code is missing, open **Setup** in the page header and follow it.

## Update
Click **Update** in the page header when it appears (zip installs), or run `git pull` (git installs). See `README.md` for details.

## Uninstall
Stop the server (Ctrl+C) and delete the folder. Nothing is installed system-wide and no project is modified by the scanner.
