"""Deterministic Antigravity PreToolUse command guard for run_command invocations."""

from __future__ import annotations

import json
import re
import sys

# High-severity patterns that must always be denied immediately.
DENY_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(
            r"(?:^|[;&|\s])(?:sudo\s+)?rm\s+"
            r"(?:-[a-zA-Z]*[rR][a-zA-Z]*f|-[a-zA-Z]*f[a-zA-Z]*[rR]|"
            r"(?:-[rRfF]|--recursive|--force)\s+(?:-[rRfF]|--recursive|--force))\s+"
            r"(?:/(?:\s*$|[;&|])|/\*|/etc\b|/usr\b|/bin\b|/sbin\b|/var\b|/lib\b|"
            r"/System\b|/Library\b|~/?(?:\s*$|[;&|*])|\$HOME/?(?:\s*$|[;&|*]))"
        ),
        "Destructive recursive removal targeting system or home root",
    ),
    (
        re.compile(
            r"(?:^|[;&|\s])(?:sudo\s+)?rm\b[^;&|\n]*"
            r"(?:^|\s)(?:/etc/|/usr/|/System/|~/\.ssh|\$HOME/\.ssh)"
        ),
        "Destructive removal targeting protected system or SSH paths",
    ),
    (
        re.compile(
            r"(?:~/\.ssh|\$HOME/\.ssh|~/\.aws|\$HOME/\.aws|"
            r"~/\.gnupg|\$HOME/\.gnupg|/etc/shadow|/etc/sudoers)"
        ),
        "Access to protected credentials or system authentication files",
    ),
    (
        re.compile(r">\s*(?:/etc/|/usr/|/bin/|/sbin/|/System/|~/\.ssh|\$HOME/\.ssh)"),
        "Redirected write outside workspace into protected system or SSH paths",
    ),
    (
        re.compile(r"(?:^|[;&|\s])(?:mkfs\b|dd\s+[^;&|\n]*of=/dev/|:\(\)\s*\{)"),
        "Dangerous disk overwrite or fork bomb pattern",
    ),
    (
        re.compile(r"(?:curl|wget)\b[^|\n]*\|\s*(?:sudo\s+)?(?:sh|bash|zsh)\b"),
        "Piped remote script execution is denied",
    ),
)

# Sensitive operations requiring explicit operator confirmation.
ASK_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(r"(?:^|[;&|\s])git\s+push\b"),
        "Remote git push requires operator confirmation",
    ),
    (
        re.compile(r"(?:^|[;&|\s])gh\s+(?:repo|release)\s+delete\b"),
        "Remote GitHub deletion requires operator confirmation",
    ),
    (
        re.compile(
            r"(?:^|[;&|\s])(?:sudo\s+)?rm\b[^;&|\n]*"
            r"(?:\s~/|\s\$HOME/|\s\.\.(?:/|\s|$)|\s/(?!tmp/))"
        ),
        "File deletion outside workspace requires operator confirmation",
    ),
    (
        re.compile(r"(?:^|[;&|\s])sudo\b"),
        "Privileged sudo execution requires operator confirmation",
    ),
)

# Safe local development prefixes auto-allowed when every command segment matches.
ALLOW_SEGMENT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"^task(?:\s+(?:--list|-l|(?:check|test|lint|format|dev|install|ci|clean|build|"
        r"plugin|ui)(?::[a-zA-Z0-9_-]+)?(?:\s+.*)?))?$"
    ),
    re.compile(
        r"^git\s+(?:status|diff|add|commit|branch|checkout|switch|log|restore|"
        r"clean|show|rev-parse|symbolic-ref|remote|init)\b"
    ),
    re.compile(r"^uv\s+(?:run|sync|pip|venv|tool|lock|version|--version)\b"),
    re.compile(r"^(?:pytest|ruff|python3?|agymissioncontrol)\b"),
    re.compile(r"^agy\s+(?:run|status|gate|models|-p|--print|--version|--help)\b"),
    re.compile(r"^(?:ls|cat|head|tail|grep|rg|find|mkdir|touch|echo|pwd|which|test|rm)\b"),
)


def extract_command_line(payload: dict) -> str:
    """Extracts CommandLine from Antigravity toolCall payload or flat test fallback."""
    tool_call = payload.get("toolCall")
    if isinstance(tool_call, dict):
        args = tool_call.get("args")
        if isinstance(args, dict) and isinstance(args.get("CommandLine"), str):
            return args["CommandLine"]
    flat_cmd = payload.get("CommandLine")
    if isinstance(flat_cmd, str):
        return flat_cmd
    return ""


def _strip_env_prefix(segment: str) -> str:
    """Strips leading VAR=VALUE assignments for segment classification."""
    return re.sub(
        r"^(?:[A-Z_][A-Z0-9_]*=(?:\"[^\"]*\"|'[^']*'|[^\s]+)\s+)+",
        "",
        segment.strip(),
    )


def evaluate_command(command: str) -> tuple[str, str]:
    """Classifies a shell command string into allow, ask, or deny."""
    cmd = command.strip()
    if not cmd:
        return "allow", "Empty command"

    for pattern, reason in DENY_PATTERNS:
        if pattern.search(cmd):
            return "deny", reason

    for pattern, reason in ASK_PATTERNS:
        if pattern.search(cmd):
            return "ask", reason

    # Disallow hidden subshell substitutions from bypassing segment checks.
    if "$(" in cmd or "`" in cmd:
        return "ask", "Command substitution requires operator confirmation"

    segments = [seg.strip() for seg in re.split(r"\s*(?:\|\||&&|\||;|\n)\s*", cmd) if seg.strip()]
    if not segments:
        return "allow", "Empty command"

    for seg in segments:
        normalized = _strip_env_prefix(seg)
        if not any(pattern.match(normalized) for pattern in ALLOW_SEGMENT_PATTERNS):
            return "ask", f"Unrecognized command segment requires confirmation: {normalized}"

    return "allow", "Safe local development command"


def main() -> int:
    """Reads Antigravity hook JSON on stdin and prints decision JSON to stdout."""
    raw = sys.stdin.read().strip()
    if not raw:
        print(json.dumps({"decision": "allow", "reason": "Empty hook payload"}))
        return 0

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        print(json.dumps({"decision": "ask", "reason": "Invalid hook JSON payload"}))
        return 0

    if not isinstance(payload, dict):
        print(json.dumps({"decision": "ask", "reason": "Unexpected hook payload type"}))
        return 0

    cmd = extract_command_line(payload)
    decision, reason = evaluate_command(cmd)
    print(json.dumps({"decision": decision, "reason": reason}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
