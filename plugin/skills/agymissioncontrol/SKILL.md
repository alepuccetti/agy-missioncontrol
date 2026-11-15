---
name: agymissioncontrol
description: Orchestrator quota gating, zero-token sleep scheduling, credit/lock telemetry, and live Quota Cockpit status pills for Google Antigravity.
---

# Mission Control — Orchestrator Quota Governor

Use `agymissioncontrol` to keep multi-agent Antigravity workflows within rolling 5-hour and weekly quota pools, prevent redundant retries during shared pool lockouts, and surface live session status in chat and the web cockpit (`http://localhost:8484`).

## 1. Orchestrator Pre-Flight & Recovery Gating

Run deterministic CLI checks rather than guessing quota availability:

1. **Before heavy parallel phases or subagent fan-out:**
   ```bash
   agymissioncontrol status --json
   ```
   Or block with zero token burn until headroom recovers:
   ```bash
   agymissioncontrol gate --model <model> --label "<task-or-workstream>"
   ```
   - Exit `0`: Quota pool is healthy (or approved for credit fallback).
   - Exit `2`: Weekly pool or active shared pool lock is depleted — do **not** spin or retry; wait or switch model families.
   - When rate-limited mid-run, run `agymissioncontrol gate --model <model> --label "<task>"` so other active Orchestrators share the lock via global `registry.db`.

2. **Audit Spend & Manage Shared Pool Locks:**
   - Check multi-project or per-project token overhead and credit burn:
     ```bash
     agymissioncontrol report --since 24h --format markdown
     ```
   - Inspect or clear stale cross-project shared locks:
     ```bash
     agymissioncontrol locks
     agymissioncontrol unlock <model>
     ```

3. **Monitor, Wake, Stop & Cockpit Server:**
   - List active/sleeping gates across repositories: `agymissioncontrol ps`
   - Wake a sleeping gate immediately to re-check `/usage`: `agymissioncontrol wake <target>` (or `--all`)
   - Terminate a gate cleanly: `agymissioncontrol stop <target>`
   - Launch the local web Quota Cockpit: `agymissioncontrol dashboard --detach`

4. **Optional Task Planning Bootstrap:**
   - Only when bootstrapping a brand-new `TASKS.md` from an issue tracker, optionally invoke:
     ```bash
     agymissioncontrol plan --provider <github|gitlab|mock>
     ```

## 2. Slash Commands (`/missioncontrol-status`, `/mc`, `/mc-status`)

When the user invokes `/missioncontrol-status`, `/mc`, or `/mc-status` (or asks for current quota/gate status):

1. Execute the companion status widget script from this skill directory:
   ```bash
   uv run python plugin/skills/agymissioncontrol/scripts/ui_widget.py
   ```
   (Or `python3 scripts/ui_widget.py` relative to this skill folder.)
2. Return its Markdown output verbatim at the top of the response so the clickable IDE status pill (`http://localhost:8484`), model pool quota table, active shared locks, and sleeping gate countdowns remain visible in Antigravity IDE chat.
