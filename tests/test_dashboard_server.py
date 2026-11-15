"""Tests for the zero-dependency local Quota Cockpit HTTP server and `dashboard` CLI."""

from __future__ import annotations

import http.client
import json
import os
import signal
import sys
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import quote

import pytest
from typer.testing import CliRunner

from agymissioncontrol.cli import app
from agymissioncontrol.quota.ledger import record_quota_snapshots
from agymissioncontrol.quota.locks import set_shared_model_lock
from agymissioncontrol.quota.models import QuotaSnapshotRecord
from agymissioncontrol.registry import SessionState, SessionStatus, init_registry, register_session
from agymissioncontrol.ui import (
    create_dashboard_server,
    get_dashboard_pid_path,
    get_static_dir,
)

runner = CliRunner()


@contextmanager
def _running_server() -> Iterator[tuple[str, int]]:
    server = create_dashboard_server(port=0)
    host, port = server.server_address[:2]
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05})
    thread.daemon = True
    thread.start()
    try:
        yield str(host), int(port)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)


def _request(
    host: str,
    port: int,
    method: str,
    path: str,
) -> tuple[int, http.client.HTTPMessage, bytes]:
    conn = http.client.HTTPConnection(host, port, timeout=5.0)
    try:
        conn.request(method, path)
        resp = conn.getresponse()
        body = resp.read()
        return resp.status, resp.headers, body
    finally:
        conn.close()


def test_get_static_dir_supports_default_and_pyinstaller_meipass(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    default_dir = get_static_dir()
    assert (default_dir / "index.html").is_file()

    frozen_root = tmp_path / "meipass_bundle"
    frozen_static = frozen_root / "agymissioncontrol" / "ui" / "static"
    frozen_static.mkdir(parents=True)
    (frozen_static / "index.html").write_text("<html>frozen</html>", encoding="utf-8")

    monkeypatch.setattr(sys, "_MEIPASS", str(frozen_root), raising=False)
    assert get_static_dir() == frozen_static


def test_dashboard_http_server_static_assets_and_traversal_guard(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("AGY_MC_STATE_DIR", str(tmp_path / "state"))

    with _running_server() as (host, port):
        assert host == "127.0.0.1"
        status, headers, body = _request(host, port, "GET", "/")
        assert status == 200
        assert "text/html" in (headers.get("Content-Type") or "")
        assert b"Mission Control Cockpit" in body

        # Request compiled CSS/JS assets discovered in index.html
        html_text = body.decode("utf-8")
        asset_marker = "./assets/"
        assert asset_marker in html_text
        asset_name = html_text.split(asset_marker, 1)[1].split('"', 1)[0]
        asset_status, asset_headers, asset_body = _request(
            host, port, "GET", f"/assets/{asset_name}"
        )
        assert asset_status == 200
        assert "javascript" in (asset_headers.get("Content-Type") or "")
        assert b"/api/quota/status" in asset_body

        # Path traversal must be rejected before touching files outside static dir
        trav_status, _, _ = _request(host, port, "GET", "/assets/../../../../etc/passwd")
        assert trav_status == 403


def test_dashboard_rest_api_endpoints_and_controls(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("AGY_MC_STATE_DIR", str(tmp_path / "state"))
    init_registry()

    now = datetime.now(UTC)
    live_pid = os.getpid()
    register_session(
        SessionState(
            pid=live_pid,
            project_name="repo-alpha",
            project_dir=str(tmp_path / "repo-alpha"),
            label="e04-cockpit",
            model="claude-sonnet-4-6",
            quota_group="Claude and GPT models",
            state=SessionStatus.SLEEPING,
            sleep_started_at=now - timedelta(minutes=5),
            wakes_at=now + timedelta(minutes=25),
            credit_burn_usd=1.75,
            updated_at=now,
        )
    )

    record_quota_snapshots(
        [
            QuotaSnapshotRecord(
                model_family="Claude and GPT models",
                limit_type="five_hour",
                remaining_pct=0.0,
                reset_at=now + timedelta(hours=2),
                recorded_at=now - timedelta(minutes=10),
                raw_line="five_hour=0%",
            ),
            QuotaSnapshotRecord(
                model_family="Claude and GPT models",
                limit_type="weekly",
                remaining_pct=62.5,
                reset_at=now + timedelta(days=3),
                recorded_at=now - timedelta(minutes=2),
                raw_line="weekly=62.5%",
            ),
            QuotaSnapshotRecord(
                model_family="Gemini Models",
                limit_type="five_hour",
                remaining_pct=88.0,
                reset_at=now + timedelta(hours=4),
                recorded_at=now,
                raw_line="five_hour=88%",
            ),
        ]
    )
    set_shared_model_lock(
        "claude-sonnet-4-6",
        locked_until=now + timedelta(hours=1),
        reason="FIVE_HOUR_DEPLETED",
    )

    signaled: list[tuple[int, int]] = []
    monkeypatch.setattr(
        "agymissioncontrol.registry.manager.os.kill",
        lambda pid, sig: None if sig == 0 else signaled.append((pid, sig)),
    )

    with _running_server() as (host, port):
        # 1. GET /api/sessions
        st, hdrs, raw = _request(host, port, "GET", "/api/sessions")
        assert st == 200
        assert hdrs.get("Cache-Control") == "no-store"
        sessions_data = json.loads(raw.decode("utf-8"))
        assert len(sessions_data["sessions"]) == 1
        assert sessions_data["sessions"][0]["pid"] == live_pid

        # 2. GET /api/quota/status
        st, hdrs, raw = _request(host, port, "GET", "/api/quota/status")
        assert st == 200
        assert hdrs.get("Cache-Control") == "no-store"
        pools_by_name = {
            pool["model_family"]: pool for pool in json.loads(raw.decode("utf-8"))["pools"]
        }
        assert pools_by_name["Claude and GPT models"]["rolling_pct"] == 0.0
        assert pools_by_name["Claude and GPT models"]["weekly_pct"] == 62.5
        assert pools_by_name["Claude and GPT models"]["locked"] is True
        assert pools_by_name["Gemini Models"]["locked"] is False

        # 3. GET /api/quota/history with filter and invalid limit fallback
        encoded_family = quote("Claude and GPT models", safe="")
        st, _, raw = _request(
            host,
            port,
            "GET",
            f"/api/quota/history?model_family={encoded_family}&limit=notanint",
        )
        assert st == 200
        history_pts = json.loads(raw.decode("utf-8"))["points"]
        assert len(history_pts) == 2
        assert all(pt["model_family"] == "Claude and GPT models" for pt in history_pts)

        # 4. GET /api/locks & POST /api/locks/clear/<model>
        st, _, raw = _request(host, port, "GET", "/api/locks")
        assert st == 200
        assert len(json.loads(raw.decode("utf-8"))["locks"]) == 1

        st, _, raw = _request(host, port, "POST", f"/api/locks/clear/{encoded_family}")
        assert st == 200
        assert json.loads(raw.decode("utf-8")) == {
            "cleared": True,
            "model": "Claude and GPT models",
        }
        st, _, raw = _request(host, port, "GET", "/api/locks")
        assert st == 200
        assert json.loads(raw.decode("utf-8"))["locks"] == []

        # 5. Wake and Stop endpoints
        st, _, raw = _request(host, port, "POST", f"/api/wake/{live_pid}")
        assert st == 200
        assert json.loads(raw.decode("utf-8"))["woken_pids"] == [live_pid]
        st, _, raw = _request(host, port, "POST", "/api/wake-all")
        assert st == 200
        assert json.loads(raw.decode("utf-8"))["woken_pids"] == [live_pid]

        st, _, raw = _request(host, port, "POST", f"/api/stop/{live_pid}")
        assert st == 200
        assert json.loads(raw.decode("utf-8"))["stopped_pids"] == [live_pid]
        st, _, raw = _request(host, port, "POST", "/api/stop-all")
        assert st == 200
        assert json.loads(raw.decode("utf-8"))["stopped_pids"] == [live_pid]

        assert signaled == [
            (live_pid, signal.SIGUSR1),
            (live_pid, signal.SIGUSR1),
            (live_pid, signal.SIGTERM),
            (live_pid, signal.SIGTERM),
        ]

        st, _, _ = _request(host, port, "POST", "/api/wake/abc")
        assert st == 400


def test_dashboard_cli_detach_stop_and_stale_pid_handling(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("AGY_MC_STATE_DIR", str(tmp_path / "state"))

    opened_urls: list[str] = []
    monkeypatch.setattr("agymissioncontrol.ui.server.webbrowser.open", opened_urls.append)

    popen_calls: list[dict[str, object]] = []

    def _fake_popen(cmd: list[str], **kwargs: object) -> SimpleNamespace:
        popen_calls.append({"cmd": cmd, "kwargs": kwargs})
        return SimpleNamespace(pid=424242)

    monkeypatch.setattr("agymissioncontrol.ui.server.subprocess.Popen", _fake_popen)

    res_detach = runner.invoke(app, ["dashboard", "--port", "9494", "--detach"])
    assert res_detach.exit_code == 0
    assert "http://localhost:9494" in res_detach.stdout
    assert "424242" in res_detach.stdout
    assert opened_urls == ["http://localhost:9494"]
    assert popen_calls[0]["kwargs"]["start_new_session"] is True
    assert get_dashboard_pid_path().read_text(encoding="utf-8").strip() == "424242"

    # Stale PID cleanup on --stop
    monkeypatch.setattr("agymissioncontrol.ui.server.is_pid_alive", lambda _pid: False)
    res_stale = runner.invoke(app, ["dashboard", "--stop"])
    assert res_stale.exit_code == 0
    assert "stale dashboard.pid" in res_stale.stdout.lower()
    assert not get_dashboard_pid_path().exists()

    # Live PID SIGTERM on --stop
    get_dashboard_pid_path().write_text("515151\n", encoding="utf-8")
    killed: list[tuple[int, int]] = []
    monkeypatch.setattr("agymissioncontrol.ui.server.is_pid_alive", lambda _pid: True)
    monkeypatch.setattr(
        "agymissioncontrol.ui.server.os.kill",
        lambda pid, sig: killed.append((pid, sig)),
    )
    res_stop = runner.invoke(app, ["dashboard", "--stop"])
    assert res_stop.exit_code == 0
    assert killed == [(515151, signal.SIGTERM)]
    assert not get_dashboard_pid_path().exists()

    # Port conflict exit 1 in foreground mode
    with _running_server() as (_host, busy_port):
        time.sleep(0.05)
        res_busy = runner.invoke(
            app,
            ["dashboard", "--port", str(busy_port), "--no-browser"],
        )
        assert res_busy.exit_code == 1
