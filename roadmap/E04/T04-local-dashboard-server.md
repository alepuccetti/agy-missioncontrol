# Task E04-T04: Zero-Dependency Local HTTP Server (`agymissioncontrol dashboard`), `--detach`, PyInstaller Asset Resolution & REST Endpoints

- **Task ID:** `E04-T04`
- **Parent Epic:** [Epic E04: Multi-Agent Process Registry & Live Web Cockpit](./EPIC.md)
- **Status:** `[ ] TODO`
- **Execution Mode:** 🐝 Parallel
- **Primary Agent:** `core-developer`
- **Dependencies:** `E04-T01`

---

## 1. User Story & Strategic Value

> **As a** Developer managing agents across multiple codebases,  
> **I want** `agymissioncontrol dashboard` to support running in the foreground or detached in the background (`--detach`), with PyInstaller single-binary static asset resolution and `--stop` to shut it down cleanly,  
> **So that** I get an instant browser cockpit that works both from source and as a standalone compiled binary without requiring Node.js or leaving orphaned server processes.

---

## 2. Testable Acceptance Criteria

- [ ] **AC-1:** Command `agymissioncontrol dashboard` accepts flags `--port` (default: 8484), `--no-browser` (disables auto-opening browser), `--detach` (`-d`), and `--stop`.
- [ ] **AC-2:** When `--detach` is passed:
  - Spawns background process via `start_new_session=True`.
  - Writes PID to `~/.local/state/agymissioncontrol/dashboard.pid`.
  - Prints URL `http://localhost:8484` and exits parent with code `0`.
- [ ] **AC-3:** When `--stop` is passed:
  - Reads `~/.local/state/agymissioncontrol/dashboard.pid` and terminates running server via `SIGTERM`.
  - Deletes PID file and prints confirmation.
- [ ] **AC-4 (PyInstaller Asset Resolution):** Implements `get_static_dir() -> Path` that inspects `getattr(sys, "_MEIPASS", None)`. Correctly locates bundled static assets when running inside a compiled single-file PyInstaller binary without raising 404 errors.
- [ ] **AC-5:** Uses Python's standard library `http.server.ThreadingHTTPServer` (zero external Python dependencies required).
- [ ] **AC-6:** Binds strictly to `127.0.0.1` for local-only security.
- [ ] **AC-7:** Endpoints and asset routing:
  - Serves compiled static assets (`index.html`, bundled JS/CSS) from `get_static_dir()` with accurate MIME types.
  - `GET /api/sessions`: Returns JSON array of all active and sleeping sessions from `registry.db` (including nested `usage` summary).
  - `GET /api/quota/history`: Returns historical time-series array of official `quota_snapshots` from global `registry.db` for charting across all projects.
  - `POST /api/wake/<pid>`: Sends `SIGUSR1` to target PID and returns `{"success": true}`.
  - `POST /api/wake-all`: Sends `SIGUSR1` to all sleeping PIDs.
  - `POST /api/stop/<pid>`: Sends `SIGTERM` to target PID for graceful shutdown and returns `{"success": true}`.
  - `POST /api/stop-all`: Sends `SIGTERM` to all active/sleeping PIDs.
- [ ] **AC-8:** Automatically opens the default browser to `http://localhost:8484` using Python's `webbrowser.open()` (unless `--no-browser` or `--stop`).
- [ ] **AC-9:** Shuts down cleanly on `Ctrl+C` (`SIGINT`) or `SIGTERM` without leaving locked sockets.

---

## 3. Exact File Manifest

| Path | Action | Description |
| :--- | :---: | :--- |
| `src/agymissioncontrol/ui/server.py` | Create | Lightweight `ThreadingHTTPServer` handler serving static assets, PyInstaller asset resolver, detached daemonizer & REST API |
| `src/agymissioncontrol/cli.py` | Modify | Register `dashboard` subcommand with `--detach` and `--stop` in Typer CLI |
| `tests/test_dashboard_server.py` | Create | Unit tests validating HTTP endpoints, PyInstaller asset resolution, detached spawning, and shutdown handling |

---

## 4. Technical Implementation Specification

### 4.1 Server Implementation (`src/agymissioncontrol/ui/server.py`)
```python
import os
import sys
import json
import signal
import subprocess
import webbrowser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from agymissioncontrol.registry.db import get_state_dir
from agymissioncontrol.registry.manager import (
    list_active_sessions,
    stop_all_sessions,
    stop_session,
    wake_session,
)

def get_pid_file() -> Path:
    return get_state_dir() / "dashboard.pid"

def get_static_dir() -> Path:
    """Resolves static distribution directory, supporting PyInstaller single-file binary extraction."""
    if hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS) / "agymissioncontrol" / "ui" / "static"
    return Path(__file__).resolve().parent / "static"

class DashboardHandler(SimpleHTTPRequestHandler):
    def translate_path(self, path: str) -> str:
        static_dir = get_static_dir()
        relative = path.lstrip("/")
        if not relative or relative == "index.html":
            return str(static_dir / "index.html")
        return str(static_dir / relative)

    def do_GET(self):
        if self.path == "/api/sessions":
            sessions = [s.model_dump() for s in list_active_sessions()]
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(json.dumps(sessions, default=str).encode("utf-8"))
            return
        elif self.path.startswith("/api/quota/history"):
            from agymissioncontrol.quota.ledger import get_quota_history
            history = get_quota_history(limit=100)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(json.dumps(history, default=str).encode("utf-8"))
            return
        super().do_GET()

    def do_POST(self):
        if self.path.startswith("/api/wake/"):
            try:
                pid = int(self.path.split("/")[-1])
                success = wake_session(pid)
                self.send_response(200 if success else 400)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"success": success}).encode("utf-8"))
            except ValueError:
                self.send_error(400, "Invalid PID")
        elif self.path == "/api/wake-all":
            sessions = list_active_sessions()
            woken = [s.pid for s in sessions if s.state == "SLEEPING" and wake_session(s.pid)]
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"woken_pids": woken}).encode("utf-8"))
        elif self.path.startswith("/api/stop/"):
            try:
                pid = int(self.path.split("/")[-1])
                success = stop_session(pid)
                self.send_response(200 if success else 400)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"success": success}).encode("utf-8"))
            except ValueError:
                self.send_error(400, "Invalid PID")
        elif self.path == "/api/stop-all":
            stopped = stop_all_sessions()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"stopped_pids": stopped}).encode("utf-8"))
        else:
            self.send_error(404)

def stop_dashboard():
    pid_file = get_pid_file()
    if not pid_file.exists():
        print("No running dashboard instance found.")
        return
    try:
        pid = int(pid_file.read_text().strip())
        os.kill(pid, signal.SIGTERM)
        pid_file.unlink(missing_ok=True)
        print(f"Dashboard (PID {pid}) stopped.")
    except Exception as e:
        print(f"Failed to stop dashboard: {e}")
        pid_file.unlink(missing_ok=True)

def run_dashboard(port: int = 8484, open_browser: bool = True):
    pid_file = get_pid_file()
    pid_file.parent.mkdir(parents=True, exist_ok=True)
    if pid_file.exists():
        try:
            existing_pid = int(pid_file.read_text().strip())
            from agymissioncontrol.registry.manager import is_pid_alive
            if is_pid_alive(existing_pid):
                print(f"Dashboard is already running (PID {existing_pid}) at http://localhost:{port}")
                if open_browser:
                    webbrowser.open(f"http://localhost:{port}")
                return
        except ValueError:
            pass

    try:
        server = ThreadingHTTPServer(("127.0.0.1", port), DashboardHandler)
    except OSError:
        print(f"Error: Port {port} is already bound. Pass a different port with --port <PORT>.")
        return

    pid_file.write_text(str(os.getpid()))
    url = f"http://localhost:{port}"
    print(f"agymissioncontrol dashboard running at {url} (Press Ctrl+C to stop)")
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except (KeyboardInterrupt, SystemExit):
        server.server_close()
        pid_file.unlink(missing_ok=True)
        print("\nDashboard stopped.")
```

---

## 5. Verification & Definition of Done

- [ ] Unit test starts server in background thread, issues `GET /api/sessions`, and asserts 200 OK with valid JSON.
- [ ] Mock test verifying that setting `sys._MEIPASS` routes static file lookups to the temporary extraction directory.
- [ ] Unit test verifies `--detach` forks process, writes `dashboard.pid`, and `--stop` cleanly terminates it.
- [ ] Test verifying that `POST /api/wake/<pid>` triggers `wake_session()`.
- [ ] `uv run pytest tests/test_dashboard_server.py` passes 100%.
