# Task E01-T02: Quota Telemetry Abstraction, Cross-Project Quota Cache & Overhead Accounting

- **Task ID:** `E01-T02`
- **Parent Epic:** [Epic E01: Quota Governor, Dual-Tier Quota & Session Circuit Breakers (`gate` / `status`)](./EPIC.md)
- **Status:** `[ ] TODO`
- **Execution Mode:** 🧠 Deep Verify
- **Primary Agent:** `core-developer`
- **Dependencies:** `E01-T01`

---

## 1. User Story & Strategic Value

> **As an** Orchestrator and Cost Controller running multiple background agents across repositories,  
> **I want** a telemetry abstraction layer that tracks dual-tier quota metrics (5-hour rolling + weekly ceiling), shares a cross-project model lockout cache in SQLite, and measures the tool's own overhead alongside productive subagent tokens,  
> **So that** `agymissioncontrol` reports exact rolling/weekly quota, prevents sibling processes on other projects from making redundant doomed API calls when a model pool is depleted, and calculates prompt cache hit rates and estimated financial costs.

---

## 2. Testable Acceptance Criteria

- [ ] **AC-1 (Dual-Tier Quota Model):** `src/agymissioncontrol/quota/models.py` defines `ModelQuota` with:
  - `rolling_window_remaining_pct: float`
  - `rolling_reset_epoch: Optional[datetime]`
  - `weekly_remaining_pct: Optional[float]`
  - `weekly_reset_epoch: Optional[datetime]`
  - `is_locked_out: bool`
  - `is_weekly_depleted: bool` (remaining weekly quota below weekly threshold)
  - `is_rolling_depleted: bool` (remaining 5-hour quota below rolling threshold)
- [ ] **AC-2 (Tool Overhead & Credit Telemetry Accounting Model):** `src/agymissioncontrol/quota/models.py` defines `UsageTelemetry`:
  - `work_tokens: int` (tokens spent on agent code generation & analysis)
  - `overhead_tokens: int` (tokens spent by Mission Control on planning, prompt assembly, error diagnostics, and verification)
  - `total_tokens: int` (`work_tokens + overhead_tokens`)
  - `overhead_pct: float` (`(overhead_tokens / total_tokens) * 100`)
  - `cache_read_tokens: int` (cached input tokens)
  - `cache_hit_rate: float` (`cache_read_tokens / (prompt_tokens + cache_read_tokens) * 100`)
  - `retry_tax_tokens: int` (cumulative tokens burned across retry attempts)
  - `estimated_cost_usd: float` (derived from model token pricing table)
  - `is_credit_funded: bool` (true if executed during quota exhaustion under active credit policy)
  - `credit_burn_usd: float` (incremental dollar spend drawn from account AI credits)
  - `credit_trigger_reason: Optional[str]` (`"weekly_depleted"` or `"rolling_bypassed"`)
- [ ] **AC-3 (Cross-Project Quota Lock Cache in SQLite):**
  - Uses `~/.local/state/agymissioncontrol/registry.db` table `model_locks` (`model TEXT PRIMARY KEY, locked_until TIMESTAMP, reason TEXT, updated_at TIMESTAMP`), keyed by the canonical quota group returned by `resolve_quota_group(model)`.
  - When any process detects a rate-limit (429) or drops below threshold, records `locked_until` epoch for that quota group.
  - Sibling processes check `model_locks` before querying telemetry; if active lock exists, reuses cached reset epoch and skips redundant CLI/API calls.
- [ ] **AC-4 (Abstract Provider & Model-to-Group Mapper):**
  - Abstract class `QuotaProvider` defines `get_snapshot() -> QuotaSnapshot` and `get_model_quota(model_name: str) -> ModelQuota`.
  - `resolve_quota_group(model_name: str) -> str` deterministically maps individual model identifiers (`claude-3-7-sonnet`, `claude-opus`, `gpt-oss` $\rightarrow$ `"Claude and GPT models"`, `gemini-4-argon*` $\rightarrow$ `"Gemini 4 Argon"`, `gemini*` $\rightarrow$ `"Gemini Models"`).
- [ ] **AC-5 (Directional Allowance & Cost Accounting):**
  - Hard metrics are tracked from agent runs: `work_tokens`, `overhead_tokens`, `cache_read_tokens`, `cost_usd`, `overhead_pct`.
  - Directional allowance % spent is tracked as an estimated telemetry metric for analytics/dashboard, but is explicitly NOT used for go/no-go decisions.
  - Gating decisions strictly evaluate authoritative values from `agy -p "/usage" --output-format json`.
  - `calculate_cost_usd(usage: UsageTelemetry, model: str) -> float` applies model token pricing.
- [ ] **AC-6 (Authoritative CLI JSON API Parsing):**
  - `AgyCliProvider` executes `agy -p "/usage" --output-format json` via subprocess without pipe hacks.
  - Direct JSON deserialization parses `command.data.groups`:
    - Model family: `Gemini Models`, `Claude and GPT models`, `Gemini 4 Argon`, etc.
    - Limit type / window: `5h` -> `five_hour`, `weekly` -> `weekly`.
    - Remaining %: `remaining_fraction * 100` (e.g. `0.88256` -> `88.26%`).
    - Reset timestamp: parses exact ISO 8601 UTC timestamp `reset_time` (e.g. `"2026-11-11T10:12:48Z"`).
- [ ] **AC-7 (Time-Series Snapshot Recording):**
  - Every time `agy -p "/usage" --output-format json` is queried, records the readings with UTC timestamps into `quota_snapshots` in both `<project_dir>/.agymissioncontrol/missioncontrol.db` and global `~/.local/state/agymissioncontrol/registry.db` (`timestamp`, `model_family`, `limit_type`, `remaining_pct`, `reset_at`).
  - Provides unified, account-wide time-series history for dashboard trend charts regardless of working directory.
- [ ] **AC-8 (Deterministic Mock Provider):** `MockProvider` supports injecting calibrated pool capacities, overhead token simulations, cross-project lock simulations, credit burn simulations, and deterministic pricing for unit testing.

---

## 3. Exact File Manifest

| Path | Action | Description |
| :--- | :---: | :--- |
| `src/agymissioncontrol/quota/models.py` | Create/Modify | `ModelQuota`, `QuotaImpact`, `QuotaSnapshot`, `UsageTelemetry`, `PricingModel` |
| `src/agymissioncontrol/quota/provider.py` | Create/Modify | `QuotaProvider` ABC, `estimate_quota_impact`, cost calculator & auto-calibration logic |
| `src/agymissioncontrol/quota/locks.py` | Create | Cross-project SQLite model lock reader and writer |
| `tests/test_quota_providers.py` | Create/Modify | Unit tests validating dual-tier quota parsing, overhead calculation, cross-project locks, and cost estimations |

---

## 4. Technical Implementation Specification

### 4.1 Cross-Project Model Lock Cache (`src/agymissioncontrol/quota/locks.py`)
```python
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

def get_registry_db_path() -> Path:
    state_dir = Path(os.environ.get("AGY_MC_STATE_DIR") or (Path.home() / ".local" / "state" / "agymissioncontrol"))
    return state_dir / "registry.db"

def init_model_locks(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS model_locks (
            model TEXT PRIMARY KEY,
            locked_until TIMESTAMP NOT NULL,
            reason TEXT NOT NULL,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)

def check_shared_model_lock(model: str) -> Optional[datetime]:
    """Returns reset epoch if another project currently locked this model."""
    db_path = get_registry_db_path()
    if not db_path.exists():
        return None
    try:
        conn = sqlite3.connect(str(db_path), timeout=30.0)
        conn.execute("PRAGMA busy_timeout = 30000;")
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT locked_until FROM model_locks WHERE model = ? AND datetime(locked_until) > datetime('now')",
            (model,)
        ).fetchone()
        conn.close()
        if row and row["locked_until"]:
            return datetime.fromisoformat(row["locked_until"])
    except Exception:
        pass
    return None

def set_shared_model_lock(model: str, locked_until: datetime, reason: str = "QUOTA_DEPLETED") -> None:
    """Broadcasts a model lock so sibling processes skip redundant checks."""
    db_path = get_registry_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        conn = sqlite3.connect(str(db_path), timeout=30.0)
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA busy_timeout = 30000;")
        init_model_locks(conn)
        conn.execute("""
            INSERT INTO model_locks (model, locked_until, reason, updated_at)
            VALUES (?, ?, ?, datetime('now'))
            ON CONFLICT(model) DO UPDATE SET
                locked_until = excluded.locked_until,
                reason = excluded.reason,
                updated_at = datetime('now');
        """, (model, locked_until.isoformat(), reason))
        conn.commit()
        conn.close()
    except Exception:
        pass
```

### 4.2 Authoritative Telemetry Parser (`agy -p "/usage" --output-format json`)
```python
import json
import subprocess
from datetime import datetime, timezone
from typing import List, Optional
from agymissioncontrol.quota.models import QuotaSnapshotRecord

def resolve_quota_group(model_name: str) -> str:
    """Maps an individual model name to its authoritative Antigravity quota group."""
    m = model_name.strip().lower()
    if m in ("gemini models", "claude and gpt models", "gemini 4 argon"):
        # Preserve official casing if already a group name
        if "argon" in m:
            return "Gemini 4 Argon"
        if "claude" in m:
            return "Claude and GPT models"
        return "Gemini Models"
    if "argon" in m or "orchestrator" in m:
        return "Gemini 4 Argon"
    if any(k in m for k in ("claude", "sonnet", "opus", "haiku", "gpt", "3p")):
        return "Claude and GPT models"
    return "Gemini Models"

def fetch_usage_json() -> dict:
    """Invokes agy in non-interactive print mode requesting structured JSON usage."""
    res = subprocess.run(
        ["agy", "-p", "/usage", "--output-format", "json"],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(res.stdout)

def parse_usage_json(payload: dict) -> List[QuotaSnapshotRecord]:
    records = []
    groups = payload.get("command", {}).get("data", {}).get("groups", [])
    now_utc = datetime.now(timezone.utc)
    for g in groups:
        family = g.get("name", "")
        for b in g.get("buckets", []):
            window = b.get("window", "")  # "5h" or "weekly"
            limit_type = "five_hour" if window == "5h" else "weekly"
            fraction = b.get("remaining_fraction", 1.0)
            remaining_pct = round(fraction * 100.0, 2)
            reset_str = b.get("reset_time", "")
            
            # Exact ISO 8601 UTC timestamp parsing
            if reset_str:
                reset_dt = datetime.fromisoformat(reset_str.replace("Z", "+00:00"))
            else:
                reset_dt = now_utc

            records.append(
                QuotaSnapshotRecord(
                    model_family=family,
                    limit_type=limit_type,
                    remaining_pct=remaining_pct,
                    reset_at=reset_dt,
                    recorded_at=now_utc,
                    raw_line=f"{family} | {b.get('name')} | {remaining_pct}% | {reset_str}",
                )
            )
    return records
```

---

## 5. Verification & Definition of Done

- [ ] Unit tests verify `check_shared_model_lock` returns cached lock when set by another process.
- [ ] Unit tests verify `parse_usage_json` parses official `agy -p "/usage" --output-format json` payloads accurately without regex guessing.
- [ ] Unit tests verify `UsageTelemetry.overhead_pct` computes accurately across varying ratios.
- [ ] Unit tests verify `calculate_cost_usd` returns exact USD values for Gemini and Claude models.
- [ ] Unit tests verify `quota_snapshots` records are inserted with timestamps for time-series charting.
- [ ] `uv run pytest tests/test_quota_providers.py` passes 100%.
