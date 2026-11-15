"""Deterministic Antigravity PreToolUse command guard for run_command invocations."""

from __future__ import annotations

import json
import re
import shlex
import sys

# High-severity patterns checked across raw commands and normalized segments.
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
)

_PIPE_REMOTE_SCRIPT = re.compile(
    r"(?:curl|wget)\b[^|\n]*\|\s*(?:[^\s;|&]*/)?(?:sudo\s+)?(?:sh|bash|zsh)\b"
)

# Sensitive operations requiring explicit operator confirmation.
ASK_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
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

_SAFE_GIT_SUBCOMMANDS: frozenset[str] = frozenset(
    {
        "status",
        "diff",
        "add",
        "commit",
        "branch",
        "checkout",
        "switch",
        "log",
        "restore",
        "clean",
        "show",
        "rev-parse",
        "symbolic-ref",
        "remote",
        "init",
    }
)

_GIT_OPTIONS_WITH_ARG: frozenset[str] = frozenset(
    {
        "-C",
        "-c",
        "--git-dir",
        "--work-tree",
        "--namespace",
        "--super-prefix",
        "--config-env",
        "--exec-path",
    }
)

# Safe local development prefixes auto-allowed when every command segment matches.
ALLOW_SEGMENT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"^task(?:\s+(?:--list|-l|(?:check|test|lint|format|dev|install|ci|clean|build|"
        r"plugin|ui)(?::[a-zA-Z0-9_-]+)?(?:\s+.*)?))?$"
    ),
    re.compile(r"^uv\s+(?:run|sync|pip|venv|tool|lock|version|--version)\b"),
    re.compile(r"^(?:pytest|ruff|python3?(?:\.\d+)?|agymissioncontrol)\b"),
    re.compile(r"^agy\s+(?:run|status|gate|models|-p|--print|--version|--help)\b"),
    re.compile(r"^(?:ls|cat|head|tail|grep|rg|find|mkdir|touch|echo|pwd|which|test)\b"),
)

_ENV_PREFIX_RE = re.compile(r"^(?:[A-Z_a-z][A-Z0-9_a-z]*=(?:\"[^\"]*\"|'[^']*'|[^\s]+)\s+)+")
_WRAPPER_PREFIX_RE = re.compile(
    r"^(?:command(?:\s+-[pvV]+)*|builtin|nohup|env(?:\s+-[A-Za-z]+)*)\s+"
)
_LEADING_PATH_RE = re.compile(r"^[^\s'\"]*/([^/\s'\"]+)(?=\s|$)")


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


def _split_shell_segments(command: str) -> tuple[list[str], bool]:
    """Split on unquoted shell operators (`&&`, `||`, `|`, `;`, newline)."""
    segments: list[str] = []
    buf: list[str] = []
    quote: str | None = None
    i = 0
    length = len(command)

    while i < length:
        ch = command[i]
        if quote is None:
            if ch in ("'", '"'):
                quote = ch
                buf.append(ch)
                i += 1
            elif ch == "\\" and i + 1 < length:
                buf.append(ch)
                buf.append(command[i + 1])
                i += 2
            elif command[i : i + 2] in ("&&", "||"):
                seg = "".join(buf).strip()
                if seg:
                    segments.append(seg)
                buf.clear()
                i += 2
            elif ch in (";", "|", "\n"):
                seg = "".join(buf).strip()
                if seg:
                    segments.append(seg)
                buf.clear()
                i += 1
            else:
                buf.append(ch)
                i += 1
        elif quote == "'":
            if ch == "'":
                quote = None
            buf.append(ch)
            i += 1
        else:
            if ch == "\\" and i + 1 < length:
                buf.append(ch)
                buf.append(command[i + 1])
                i += 2
            else:
                if ch == '"':
                    quote = None
                buf.append(ch)
                i += 1

    trailing = "".join(buf).strip()
    if trailing:
        segments.append(trailing)
    return segments, quote is not None


def _has_command_substitution(command: str) -> bool:
    """Return ``True`` when unquoted or double-quoted `$(...)` / backticks appear."""
    quote: str | None = None
    i = 0
    length = len(command)

    while i < length:
        ch = command[i]
        if quote == "'":
            if ch == "'":
                quote = None
            i += 1
            continue
        if ch == "\\" and i + 1 < length:
            i += 2
            continue
        if quote is None and ch in ("'", '"'):
            quote = ch
            i += 1
            continue
        if quote == '"' and ch == '"':
            quote = None
            i += 1
            continue
        if ch == "`" or command[i : i + 2] == "$(":
            return True
        i += 1
    return False


def _strip_env_prefix(segment: str) -> str:
    """Strip env vars, shell wrappers, and executable path prefixes from `segment`."""
    current = segment.strip()
    while True:
        updated = _ENV_PREFIX_RE.sub("", current, count=1).strip()
        updated = _WRAPPER_PREFIX_RE.sub("", updated, count=1).strip()
        updated = _LEADING_PATH_RE.sub(r"\1", updated, count=1).strip()
        if updated.startswith("sudo "):
            after_sudo = updated[5:].strip()
            normalized_after = _strip_env_prefix(after_sudo)
            updated = f"sudo {normalized_after}" if normalized_after else "sudo"
        if updated == current:
            return current
        current = updated


def _extract_git_subcommand(tokens: list[str]) -> str | None:
    """Return git subcommand token after global options like `-C <path>`."""
    idx = 1
    while idx < len(tokens):
        tok = tokens[idx]
        if tok == "--":
            idx += 1
            return tokens[idx] if idx < len(tokens) else None
        if tok in _GIT_OPTIONS_WITH_ARG:
            idx += 2
            continue
        if tok.startswith("-C") or tok.startswith("-c"):
            idx += 1
            continue
        if tok.startswith("--") and "=" in tok:
            opt_name = tok.split("=", 1)[0]
            if opt_name in _GIT_OPTIONS_WITH_ARG:
                idx += 1
                continue
        if tok.startswith("-"):
            idx += 1
            continue
        return tok
    return None


def _python_uses_inline_code(tokens: list[str]) -> bool:
    """Return ``True`` when Python tokens pass `-c` inline execution."""
    for tok in tokens[1:]:
        if tok in ("-", "--"):
            break
        if tok in ("-m", "-c") or tok.startswith("-c"):
            if tok == "-m":
                return False
            return True
        if tok.startswith("-") and not tok.startswith("--") and "c" in tok[1:]:
            return True
        if not tok.startswith("-"):
            break
    return False


def _evaluate_normalized_segment(normalized: str) -> tuple[str, str] | None:
    """Classify git/python/rm/unmatched-quote segments; returns None when undecided."""
    try:
        tokens = shlex.split(normalized)
    except ValueError:
        return "ask", f"Unmatched quotes in command segment: {normalized}"

    if not tokens:
        return "allow", "Empty segment"

    exe = tokens[0]
    if exe == "rm":
        return "ask", "File deletion via rm requires operator confirmation"

    if exe == "git":
        subcmd = _extract_git_subcommand(tokens)
        if subcmd == "push":
            return "ask", "Remote git push requires operator confirmation"
        if subcmd in _SAFE_GIT_SUBCOMMANDS:
            return "allow", "Safe local git command"
        return "ask", f"Git subcommand '{subcmd or 'unknown'}' requires confirmation"

    if exe in {"python", "python3"} or exe.startswith("python3."):
        if _python_uses_inline_code(tokens):
            return "ask", "Inline python -c execution requires operator confirmation"

    if exe == "uv" and len(tokens) >= 3 and tokens[1] == "run":
        sub_norm = _strip_env_prefix(" ".join(tokens[2:]))
        sub_tokens = sub_norm.split()
        if (
            sub_tokens
            and (sub_tokens[0] in {"python", "python3"} or sub_tokens[0].startswith("python3."))
            and _python_uses_inline_code(sub_tokens)
        ):
            return "ask", "Inline python -c execution requires operator confirmation"

    return None


def evaluate_command(command: str) -> tuple[str, str]:
    """Classifies a shell command string into allow, ask, or deny."""
    cmd = command.strip()
    if not cmd:
        return "allow", "Empty command"

    if _PIPE_REMOTE_SCRIPT.search(cmd):
        return "deny", "Piped remote script execution is denied"

    for pattern, reason in DENY_PATTERNS:
        if pattern.search(cmd):
            return "deny", reason

    segments, has_unclosed_quote = _split_shell_segments(cmd)
    normalized_segments = [_strip_env_prefix(seg) for seg in segments]

    for normalized in normalized_segments:
        for pattern, reason in DENY_PATTERNS:
            if pattern.search(normalized):
                return "deny", reason

    for normalized in normalized_segments:
        for pattern, reason in ASK_PATTERNS:
            if pattern.search(normalized):
                return "ask", reason

    if _has_command_substitution(cmd):
        return "ask", "Command substitution requires operator confirmation"

    if has_unclosed_quote:
        return "ask", "Unclosed shell quote requires operator confirmation"

    if not normalized_segments:
        return "allow", "Empty command"

    for normalized in normalized_segments:
        special = _evaluate_normalized_segment(normalized)
        if special is not None:
            decision, reason = special
            if decision != "allow":
                return decision, reason
            continue
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
