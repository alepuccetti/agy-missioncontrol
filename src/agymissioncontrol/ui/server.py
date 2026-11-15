"""Zero-dependency local stdlib HTTP server for the Antigravity Quota Cockpit."""

from __future__ import annotations

import json
import mimetypes
import os
import signal
import subprocess
import sys
import threading
import webbrowser
from datetime import UTC, datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

from agymissioncontrol.quota.ledger import get_quota_history
from agymissioncontrol.quota.locks import (
    clear_shared_model_lock,
    list_shared_model_locks,
    resolve_quota_group,
)
from agymissioncontrol.registry import (
    get_registry_db,
    get_state_dir,
    init_registry,
    is_pid_alive,
    list_active_sessions,
    stop_all_sessions,
    stop_session,
    wake_session,
    wake_target,
)

_HOST = "127.0.0.1"
_MIME_OVERRIDES = {
    ".css": "text/css; charset=utf-8",
    ".html": "text/html; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".mjs": "application/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
}


def get_static_dir() -> Path:
    """Resolve compiled frontend static directory, supporting PyInstaller `_MEIPASS`."""
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return Path(meipass) / "agymissioncontrol" / "ui" / "static"
    return Path(__file__).resolve().parent / "static"


def get_dashboard_pid_path() -> Path:
    """Return path to `dashboard.pid` under the configured state directory."""
    state_dir = get_state_dir()
    state_dir.mkdir(parents=True, exist_ok=True)
    return state_dir / "dashboard.pid"


def _remove_pid_file(expected_pid: int | None = None) -> None:
    pid_path = get_dashboard_pid_path()
    if not pid_path.is_file():
        return
    if expected_pid is not None:
        try:
            recorded = int(pid_path.read_text(encoding="utf-8").strip())
        except ValueError:
            recorded = None
        if recorded != expected_pid:
            return
    pid_path.unlink(missing_ok=True)


def stop_detached_dashboard() -> tuple[bool, int | None, str]:
    """Stop a running detached dashboard server recorded in `dashboard.pid`."""
    pid_path = get_dashboard_pid_path()
    if not pid_path.is_file():
        return False, None, "No detached dashboard server is running (dashboard.pid not found)."

    raw_pid = pid_path.read_text(encoding="utf-8").strip()
    try:
        pid = int(raw_pid)
    except ValueError:
        pid_path.unlink(missing_ok=True)
        return False, None, f"Removed invalid dashboard.pid file ({raw_pid!r})."

    if not is_pid_alive(pid):
        pid_path.unlink(missing_ok=True)
        return False, pid, f"Removed stale dashboard.pid for inactive PID {pid}."

    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError, PermissionError:
        pid_path.unlink(missing_ok=True)
        return False, pid, f"Removed stale dashboard.pid for inactive PID {pid}."

    pid_path.unlink(missing_ok=True)
    return True, pid, f"Stopped detached dashboard server (PID {pid})."


def spawn_detached_dashboard(port: int = 8484, open_browser: bool = True) -> int:
    """Launch `agymissioncontrol dashboard` in a detached background session."""
    if getattr(sys, "frozen", False):
        cmd = [sys.executable, "dashboard", "--port", str(port), "--no-browser"]
    else:
        cmd = [
            sys.executable,
            "-m",
            "agymissioncontrol.cli",
            "dashboard",
            "--port",
            str(port),
            "--no-browser",
        ]

    proc = subprocess.Popen(
        cmd,
        start_new_session=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
    )
    get_dashboard_pid_path().write_text(f"{proc.pid}\n", encoding="utf-8")
    if open_browser:
        webbrowser.open(f"http://localhost:{port}")
    return proc.pid


def _classify_limit_type(limit_type: str) -> str:
    lowered = limit_type.lower().strip()
    if "week" in lowered:
        return "weekly"
    return "rolling"


def build_quota_status_payload() -> dict[str, Any]:
    """Build latest per-pool quota availability and lock status from `registry.db`."""
    init_registry()
    conn = get_registry_db()
    try:
        rows = conn.execute(
            """
            SELECT timestamp, model_family, limit_type, remaining_pct, reset_at
            FROM quota_snapshots
            ORDER BY timestamp DESC, id DESC
            """
        ).fetchall()
    finally:
        conn.close()

    locks = list_shared_model_locks(include_expired=False)
    locked_keys = {str(lock["model"]) for lock in locks} | {
        resolve_quota_group(str(lock["model"])) for lock in locks
    }

    pools_map: dict[str, dict[str, Any]] = {}
    for row in rows:
        family = str(row["model_family"])
        entry = pools_map.setdefault(
            family,
            {
                "model_family": family,
                "rolling_pct": None,
                "rolling_reset_at": None,
                "weekly_pct": None,
                "weekly_reset_at": None,
                "locked": False,
            },
        )
        tier = _classify_limit_type(str(row["limit_type"]))
        pct_key = f"{tier}_pct"
        reset_key = f"{tier}_reset_at"
        if entry[pct_key] is None:
            entry[pct_key] = float(row["remaining_pct"])
            entry[reset_key] = str(row["reset_at"]) if row["reset_at"] else None

    for family, entry in pools_map.items():
        rolling_zero = entry["rolling_pct"] is not None and entry["rolling_pct"] <= 0.0
        weekly_zero = entry["weekly_pct"] is not None and entry["weekly_pct"] <= 0.0
        pool_locked = (
            family in locked_keys
            or resolve_quota_group(family) in locked_keys
            or rolling_zero
            or weekly_zero
        )
        entry["locked"] = pool_locked

    return {
        "pools": list(pools_map.values()),
        "updated_at": datetime.now(UTC).isoformat(),
    }


def build_sessions_payload() -> dict[str, Any]:
    """Return active and sleeping registered gate sessions (auto-pruning dead PIDs)."""
    sessions = list_active_sessions()
    return {"sessions": [session.model_dump(mode="json") for session in sessions]}


def build_locks_payload() -> dict[str, Any]:
    """Return active shared model locks formatted as JSON-serializable records."""
    locks = list_shared_model_locks(include_expired=False)
    serialized: list[dict[str, Any]] = []
    for item in locks:
        locked_until = item["locked_until"]
        updated_at = item["updated_at"]
        serialized.append(
            {
                "model": item["model"],
                "locked_until": (
                    locked_until.isoformat()
                    if isinstance(locked_until, datetime)
                    else str(locked_until)
                ),
                "locked_by_pid": item.get("locked_by_pid"),
                "reason": item.get("reason") or "",
                "updated_at": (
                    updated_at.isoformat() if isinstance(updated_at, datetime) else str(updated_at)
                ),
                "remaining_seconds": int(item.get("remaining_seconds", 0)),
            }
        )
    return {"locks": serialized}


def build_history_payload(
    model_family: str | None = None,
    limit: int = 200,
) -> dict[str, Any]:
    """Return chronological historical quota snapshots clamped to `[1, 1000]`."""
    clamped_limit = max(1, min(1000, limit))
    points = get_quota_history(
        project_dir=None,
        model_family=model_family or None,
        limit=clamped_limit,
    )
    return {"points": points}


class DashboardHTTPServer(ThreadingHTTPServer):
    """Multi-threaded stdlib HTTP server bound exclusively to loopback."""

    daemon_threads = True
    allow_reuse_address = True


class DashboardHandler(BaseHTTPRequestHandler):
    """HTTP request handler serving static cockpit assets and JSON control APIs."""

    server_version = "AntigravityMissionControl/0.4"

    def log_message(self, format: str, *args: Any) -> None:
        """Suppress noisy stderr request logs unless debug logging is explicitly enabled."""
        if os.environ.get("AGY_MC_HTTP_DEBUG") == "1":
            super().log_message(format, *args)

    def _send_bytes(
        self,
        status: HTTPStatus,
        body: bytes,
        content_type: str,
        *,
        no_store: bool = False,
    ) -> None:
        try:
            self.send_response(int(status))
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            if no_store:
                self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
        except BrokenPipeError, ConnectionResetError:
            return

    def _send_json(self, status: HTTPStatus, payload: Any) -> None:
        encoded = json.dumps(payload).encode("utf-8")
        self._send_bytes(status, encoded, "application/json; charset=utf-8", no_store=True)

    def _serve_static(self, url_path: str) -> None:
        static_root = get_static_dir().resolve()
        decoded = unquote(url_path).lstrip("/")
        if not decoded:
            decoded = "index.html"

        posix_parts = PurePosixPath(decoded).parts
        if any(part == ".." for part in posix_parts):
            self._send_json(HTTPStatus.FORBIDDEN, {"error": "Path traversal forbidden"})
            return

        candidate = (static_root / decoded).resolve()
        if not candidate.is_relative_to(static_root):
            self._send_json(HTTPStatus.FORBIDDEN, {"error": "Path traversal forbidden"})
            return

        if not candidate.is_file():
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "Static asset not found"})
            return

        suffix = candidate.suffix.lower()
        content_type = _MIME_OVERRIDES.get(suffix)
        if content_type is None:
            guessed, _ = mimetypes.guess_type(str(candidate))
            content_type = guessed or "application/octet-stream"
        self._send_bytes(HTTPStatus.OK, candidate.read_bytes(), content_type)

    def _drain_request_body(self) -> None:
        content_length = int(self.headers.get("Content-Length", "0") or 0)
        if content_length > 0:
            self.rfile.read(content_length)

    def do_GET(self) -> None:
        """Handle static asset downloads and read-only quota/session/lock JSON APIs."""
        parsed = urlsplit(self.path)
        path = parsed.path

        if path == "/api/sessions":
            self._send_json(HTTPStatus.OK, build_sessions_payload())
            return

        if path == "/api/quota/status":
            self._send_json(HTTPStatus.OK, build_quota_status_payload())
            return

        if path == "/api/quota/history":
            query = parse_qs(parsed.query)
            model_family = query.get("model_family", [None])[0]
            raw_limit = query.get("limit", ["200"])[0]
            try:
                limit = int(raw_limit)
            except TypeError, ValueError:
                limit = 200
            self._send_json(
                HTTPStatus.OK,
                build_history_payload(model_family=model_family, limit=limit),
            )
            return

        if path == "/api/locks":
            self._send_json(HTTPStatus.OK, build_locks_payload())
            return

        if path.startswith("/api/"):
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "Unknown API endpoint"})
            return

        self._serve_static(path)

    def do_POST(self) -> None:
        """Handle gate wake/stop and shared lock reset control endpoints."""
        self._drain_request_body()
        path = urlsplit(self.path).path

        if path == "/api/wake-all":
            woken_pids = wake_target(match_all=True)
            self._send_json(
                HTTPStatus.OK,
                {"woken_pids": woken_pids, "count": len(woken_pids)},
            )
            return

        if path == "/api/stop-all":
            stopped_pids = stop_all_sessions()
            self._send_json(
                HTTPStatus.OK,
                {"stopped_pids": stopped_pids, "count": len(stopped_pids)},
            )
            return

        if path.startswith("/api/wake/"):
            raw_pid = path.removeprefix("/api/wake/")
            if not raw_pid.isdigit():
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": f"Invalid PID: {raw_pid!r}"})
                return
            pid = int(raw_pid)
            ok = wake_session(pid)
            self._send_json(
                HTTPStatus.OK,
                {"woken_pids": [pid] if ok else [], "count": 1 if ok else 0},
            )
            return

        if path.startswith("/api/stop/"):
            raw_pid = path.removeprefix("/api/stop/")
            if not raw_pid.isdigit():
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": f"Invalid PID: {raw_pid!r}"})
                return
            pid = int(raw_pid)
            ok = stop_session(pid)
            self._send_json(
                HTTPStatus.OK,
                {"stopped_pids": [pid] if ok else [], "count": 1 if ok else 0},
            )
            return

        if path.startswith("/api/locks/clear/"):
            raw_model = unquote(path.removeprefix("/api/locks/clear/")).strip()
            if not raw_model:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": "Missing model identifier"})
                return
            resolved_model = resolve_quota_group(raw_model)
            clear_shared_model_lock(raw_model)
            self._send_json(HTTPStatus.OK, {"cleared": True, "model": resolved_model})
            return

        self._send_json(HTTPStatus.NOT_FOUND, {"error": "Unknown API endpoint"})


def create_dashboard_server(port: int = 8484) -> DashboardHTTPServer:
    """Instantiate the localhost-only `ThreadingHTTPServer` for the Quota Cockpit."""
    init_registry()
    return DashboardHTTPServer((_HOST, port), DashboardHandler)


def run_dashboard_server(port: int = 8484, open_browser: bool = True) -> None:
    """Run the cockpit HTTP server in the foreground until interrupted by `SIGINT`/`SIGTERM`."""
    server = create_dashboard_server(port=port)
    actual_port = server.server_address[1]
    current_pid = os.getpid()
    get_dashboard_pid_path().write_text(f"{current_pid}\n", encoding="utf-8")

    stop_requested = threading.Event()
    prev_int = None
    prev_term = None

    def _handle_stop(signum: int, frame: Any) -> None:
        stop_requested.set()
        threading.Thread(target=server.shutdown, daemon=True).start()

    if threading.current_thread() is threading.main_thread():
        prev_int = signal.signal(signal.SIGINT, _handle_stop)
        prev_term = signal.signal(signal.SIGTERM, _handle_stop)

    if open_browser:
        webbrowser.open(f"http://localhost:{actual_port}")

    try:
        server.serve_forever(poll_interval=0.25)
    finally:
        server.server_close()
        _remove_pid_file(expected_pid=current_pid)
        if threading.current_thread() is threading.main_thread():
            if prev_int is not None:
                signal.signal(signal.SIGINT, prev_int)
            if prev_term is not None:
                signal.signal(signal.SIGTERM, prev_term)
