# Epic E04: Multi-Agent Process Registry & Live Web Cockpit

- **Epic ID:** `E04`
- **Horizon:** Horizon 2 — Multi-Agent Registry & Live Web Cockpit (Release Milestone `v1.0.0` GA)
- **Status:** `[ ] TODO`
- **Execution Mode:** ⚡ Standard Teamwork
- **Primary Agents:** `core-developer`, `frontend-developer`
- **Dependencies:** `E00` through `E03`
- **Specification References:** [Project Specification §1 (Executive Summary)](../../README.md#1-executive-summary--purpose), [§4 (State & Concurrency: registry.db)](../../README.md#4-state--concurrency-hybrid-architecture), [§6 (CLI: ps, wake, stop, logs, dashboard)](../../README.md#6-cli-interface-specification)

---

## 1. Epic User Story & Strategic Value

> **As an** Engineering Lead running multiple autonomous agent sessions across different codebases,  
> **I want** an embedded SQLite-backed process registry (`~/.local/state/agymissioncontrol/registry.db`) and a modern, dark-themed local web cockpit built with TypeScript, Alpine.js, and Lucide icons featuring a manual refresh button and a fixed 15-second auto-refresh toggle,  
> **So that** I can open a browser dashboard and instantly see which agents are running, which are sleeping on quota limits, when each will wake up, track overhead/cost metrics, and trigger early wakeups or aborts across all my projects with zero locking collisions.

---

## 2. Epic Objective & Architectural Scope

Implement the SQLite-backed process registry in `~/.local/state/agymissioncontrol/registry.db` (WAL mode with 30s busy timeout), CLI commands `agymissioncontrol ps`, `wake`, `stop`, and `logs`, a zero-dependency local dashboard server (`agymissioncontrol dashboard` with `--detach` and PyInstaller asset resolution), a modern single-page visual dashboard (TypeScript + Alpine.js + Lucide icons + Dark Mode) with live sleep countdowns and a fixed 15-second auto-refresh button, and Antigravity IDE UI integration.

### Key Architectural Invariants Established in E04
1. **SQLite-Backed Process Registry (`registry.db`):**
   - Active and sleeping agent sessions maintain state rows in `~/.local/state/agymissioncontrol/registry.db`.
   - Uses standard library `sqlite3` in WAL mode with a 30-second busy timeout (`PRAGMA busy_timeout = 30000;`), guaranteeing atomic updates across parallel sessions without torn writes or database locking errors.
   - On wakeup or normal exit, rows are cleanly removed via `atexit`.
   - Stale records from dead processes are auto-detected via `os.kill(pid, 0)` and purged in an atomic transaction.
2. **Zero-Dependency Web Server (`agymissioncontrol dashboard`):**
   - Built directly on Python's standard library `http.server`—requires zero extra web framework dependencies (no Flask, FastAPI, or Node.js required at runtime).
   - Dynamically resolves bundled assets when running inside a PyInstaller single-file binary via `sys._MEIPASS`.
   - Supports `--detach` and `--stop` flags for background service lifecycle.
   - Serves compiled static assets from `get_static_dir()` on `localhost:8484` (configurable via `--port`).
3. **Live Web Cockpit UI (TypeScript + Alpine.js + Lucide + Dark Mode):**
   - Responsive, dark-themed frontend designed with Alpine.js reactive components, typed TypeScript data models, and modern Lucide SVG icons.
   - Aesthetic matching the Antigravity IDE dark palette (`#0f1117` base, glowing status indicators).
   - Live header summary cards: Aggregate Active Agents, Sleeping Agents, Mission Control Overhead %, Cache Hit Rate %, and Session Cost ($).
   - Includes a manual **"Refresh Now"** button with spin animation and a toggleable **"Auto-Refresh (15s)"** button with a fixed 15-second polling interval.
   - Supports one-click **"Wake Early"** button triggering POSIX signal wakeups.
4. **Process Signal Control (`SIGUSR1` & `SIGTERM`):**
   - `agymissioncontrol wake <TARGET>` sends `SIGUSR1` to break out of the sleep loop and re-verify quota immediately.
   - `agymissioncontrol stop <TARGET>` sends `SIGTERM` to gracefully abort running or sleeping background sessions.

---

## 3. Epic-Level Acceptance Criteria

- [ ] **AC-1 (Process Registry in SQLite):** `registry.db` maintains session rows with WAL mode and 30s busy timeout; dead PIDs are detected and pruned atomically.
- [ ] **AC-2 (`agymissioncontrol ps`, `wake`, `stop`, `logs` CLI):** Full process inspection and control via CLI: formatted table of sessions, signal wakeup, graceful stop, and log streaming.
- [ ] **AC-3 (`agymissioncontrol wake` CLI):** Dispatches `SIGUSR1` to target PID, project, or `--all`, triggering immediate quota re-check.
- [ ] **AC-4 (Modern Web UI):** Single-page cockpit built with TypeScript, Alpine.js, Lucide icons, Dark Mode, manual Refresh button, and a fixed 15-second Auto-Refresh toggle button.
- [ ] **AC-5 (Zero-Dependency Dashboard Server):** `agymissioncontrol dashboard` starts local HTTP server on `localhost:8484`, supports `--detach` and `--stop`, resolves assets in standalone binary mode, serves static frontend and `/api/sessions`, and opens browser automatically.

---

## 4. Tasks in This Epic

| Task ID | Title | Signal | Status |
| :--- | :--- | :---: | :---: |
| **[E04-T01](./T01-process-registry-engine.md)** | SQLite-Backed Multi-Agent Process Registry (`registry.db`), Busy Timeout & Dead PID Pruning | 🧠 Deep Verify | `[ ] TODO` |
| **[E04-T02](./T02-cli-ps-and-wake.md)** | CLI Process Management: `ps`, `wake`, `stop` & `logs` for Detached Sessions | 🐝 Parallel | `[ ] TODO` |
| **[E04-T03](./T03-web-dashboard-ui.md)** | Modern Single-Page Web Cockpit UI (TypeScript, Alpine.js, Lucide Icons, Dark Mode & 15s Auto-Refresh) | 🐝 Parallel | `[ ] TODO` |
| **[E04-T04](./T04-local-dashboard-server.md)** | Zero-Dependency Local HTTP Server (`agymissioncontrol dashboard`), `--detach`, PyInstaller Asset Resolution & REST Endpoints | 🐝 Parallel | `[ ] TODO` |
| **[E04-T05](./T05-antigravity-ui-integration.md)** | Antigravity IDE UI Status Pill & Webview Auxiliary Pane Integration | ⚡ Direct | `[ ] TODO` |

---

## 5. Epic Completion Gate (Definition of Done)

- [ ] Unit tests in `tests/test_registry.py` verify SQLite registry table initialization, WAL mode, 30s busy timeout, state updates, and stale PID pruning.
- [ ] `agymissioncontrol ps` verified to render active and sleeping processes across multiple mock project directories.
- [ ] `agymissioncontrol wake <TARGET>` and `agymissioncontrol stop <TARGET>` verified to signal targets cleanly.
- [ ] `task ui:build` compiles TypeScript + Alpine.js frontend into `src/agymissioncontrol/ui/static/`.
- [ ] `agymissioncontrol dashboard` serves web UI at `http://localhost:8484` and successfully auto-refreshes every 15 seconds when enabled.
- [ ] `task check` executes cleanly with 100% test pass rate.
