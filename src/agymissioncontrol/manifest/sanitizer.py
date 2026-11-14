"""Heuristic noise sanitization pipeline for raw issue bodies."""

from __future__ import annotations

import re

TRUNCATION_MARKER = "[... truncated log ...]"

_FENCE_PATTERN = re.compile(
    r"(^|\n)([ \t]*)(`{3,}|~{3,})([^\n]*)\n(.*?)\n[ \t]*\3[ \t]*(?=\n|$)",
    re.DOTALL,
)
_HTML_COMMENT_PATTERN = re.compile(r"<!--.*?-->", re.DOTALL)
_LINKED_BADGE_PATTERN = re.compile(r"\[!\[[^\]]*\]\([^)]+\)\]\([^)]+\)")
_IMAGE_BADGE_PATTERN = re.compile(
    r"!\[[^\]]*\]\([^)]*"
    r"(?:shields\.io|badgen\.net|badge\.svg|codecov\.io|coveralls\.io"
    r"|/actions/workflows/[^)]+/badge)"
    r"[^)]*\)",
    re.IGNORECASE,
)
_HEADING_PATTERN = re.compile(r"^(#{1,6})\s+(.+?)\s*#*$", re.MULTILINE)

_PLACEHOLDER_VALUES = {
    "",
    "-",
    "--",
    "---",
    "_",
    "n/a",
    "na",
    "none",
    "null",
    "nil",
    "tbd",
    "todo",
    "_no response_",
    "no response",
    "(none)",
}

_PLACEHOLDER_LINE_PATTERN = re.compile(
    r"^(?:[-*]\s*)?(?:"
    r"n/?a|none|null|nil|tbd|todo|_?no response_?|\.+|"
    r"<[^>\n]{1,80}>|"
    r"\[(?:insert|describe|enter|add|your)[^\]\n]{0,80}\]|"
    r"(?:enter|describe|write|paste)\s+(?:here|details|steps)[^\n]{0,60}"
    r")\.?\s*$",
    re.IGNORECASE,
)

_BOILERPLATE_HEADING_TOKENS = {
    "environment",
    "steps to reproduce",
    "to reproduce",
    "reproduction steps",
    "system information",
    "desktop",
    "smartphone",
    "additional context",
    "screenshots",
    "logs",
    "pre-submission checklist",
    "submission checklist",
    "issue checklist",
}

_PRESERVED_HEADING_TOKENS = {
    "acceptance criteria",
    "definition of done",
    "dod",
    "verification",
    "tests",
    "testing",
    "requirements",
    "scope",
    "constraints",
    "tasks",
    "dependencies",
}

_TEMPLATE_CHECKLIST_PHRASES = (
    "searched existing issues",
    "searched the existing issues",
    "read the contributing",
    "code of conduct",
    "bug report template",
    "latest version",
)


def _truncate_fenced_block(fence: str, indent: str, info: str, body: str) -> str:
    lines = body.splitlines()
    if len(lines) <= 40:
        return f"{indent}{fence}{info}\n{body}\n{indent}{fence}"
    kept = lines[:5] + [TRUNCATION_MARKER] + lines[-5:]
    truncated_body = "\n".join(kept)
    return f"{indent}{fence}{info}\n{truncated_body}\n{indent}{fence}"


def _extract_and_truncate_fences(text: str) -> tuple[str, dict[str, str]]:
    """Replace fenced code blocks with stable tokens after log truncation."""
    blocks: dict[str, str] = {}
    counter = 0

    def _replace(match: re.Match[str]) -> str:
        nonlocal counter
        prefix_nl = match.group(1)
        indent = match.group(2)
        fence = match.group(3)
        info = match.group(4)
        body = match.group(5)
        token = f"@@AGY_CODE_BLOCK_{counter}@@"
        counter += 1
        blocks[token] = _truncate_fenced_block(fence, indent, info, body)
        return f"{prefix_nl}{token}"

    replaced = _FENCE_PATTERN.sub(_replace, text)
    return replaced, blocks


def _is_placeholder_only(lines: list[str]) -> bool:
    meaningful = [line.strip() for line in lines if line.strip() and line.strip() != "---"]
    if not meaningful:
        return True
    for line in meaningful:
        lowered = line.lower().strip("`*_ ")
        if lowered in _PLACEHOLDER_VALUES:
            continue
        if _PLACEHOLDER_LINE_PATTERN.match(line):
            continue
        return False
    return True


def _is_template_checklist(heading: str, lines: list[str]) -> bool:
    normalized = heading.strip().lower().rstrip(":")
    if any(token in normalized for token in _PRESERVED_HEADING_TOKENS):
        return False

    meaningful = [line.strip() for line in lines if line.strip() and line.strip() != "---"]
    if not meaningful:
        return True

    checkbox_lines = [line for line in meaningful if re.match(r"^[-*]\s*\[[ xX]?\]\s+", line)]
    if len(checkbox_lines) != len(meaningful):
        return False

    if any(token in normalized for token in _BOILERPLATE_HEADING_TOKENS):
        return True

    lowered_blob = "\n".join(checkbox_lines).lower()
    return any(phrase in lowered_blob for phrase in _TEMPLATE_CHECKLIST_PHRASES)


def _strip_boilerplate_sections(text: str) -> str:
    """Remove template sections filled only with placeholders or boilerplate."""
    lines = text.splitlines()
    sections: list[tuple[int | None, str | None, list[str]]] = []
    current_level: int | None = None
    current_heading: str | None = None
    current_lines: list[str] = []

    for line in lines:
        match = _HEADING_PATTERN.match(line)
        if match:
            sections.append((current_level, current_heading, current_lines))
            current_level = len(match.group(1))
            current_heading = match.group(2).strip()
            current_lines = []
        else:
            current_lines.append(line)

    sections.append((current_level, current_heading, current_lines))

    kept_lines: list[str] = []
    for idx, (level, heading, body_lines) in enumerate(sections):
        if heading is None or level is None:
            kept_lines.extend(body_lines)
            continue

        normalized_heading = heading.lower().rstrip(":")
        is_preserved = any(tok in normalized_heading for tok in _PRESERVED_HEADING_TOKENS)

        # Keep container headers immediately followed by deeper subheadings
        has_child_heading = idx + 1 < len(sections) and (sections[idx + 1][0] or 0) > level
        body_nonempty = [ln for ln in body_lines if ln.strip() and ln.strip() != "---"]

        if not body_nonempty and has_child_heading:
            kept_lines.append(f"{'#' * level} {heading}")
            continue

        if not is_preserved:
            if _is_placeholder_only(body_lines):
                continue
            if _is_template_checklist(heading, body_lines):
                continue

        kept_lines.append(f"{'#' * level} {heading}")
        kept_lines.extend(body_lines)

    return "\n".join(kept_lines)


def _strip_empty_headers(text: str) -> str:
    """Iteratively remove markdown headings that have no content or non-empty children."""
    changed = True
    current = text
    while changed:
        changed = False
        lines = current.splitlines()
        heading_indices = [
            (idx, len(match.group(1)))
            for idx, line in enumerate(lines)
            if (match := _HEADING_PATTERN.match(line)) is not None
        ]
        if not heading_indices:
            break

        to_remove: set[int] = set()
        for pos, (line_idx, level) in enumerate(heading_indices):
            next_line_idx = (
                heading_indices[pos + 1][0] if pos + 1 < len(heading_indices) else len(lines)
            )
            between = [
                ln.strip() for ln in lines[line_idx + 1 : next_line_idx] if ln.strip() != "---"
            ]
            if any(between):
                continue

            next_level = heading_indices[pos + 1][1] if pos + 1 < len(heading_indices) else 0
            if next_level == 0 or next_level <= level:
                to_remove.add(line_idx)

        if to_remove:
            lines = [line for idx, line in enumerate(lines) if idx not in to_remove]
            current = "\n".join(lines)
            changed = True

    return current


def sanitize_issue_body(raw_body: str) -> str:
    """Remove HTML comments, badges, boilerplate template sections, and huge logs."""
    if not raw_body or not raw_body.strip():
        return ""

    text = raw_body.replace("\r\n", "\n").replace("\r", "\n")
    text, fenced_blocks = _extract_and_truncate_fences(text)

    text = _HTML_COMMENT_PATTERN.sub("", text)
    text = _LINKED_BADGE_PATTERN.sub("", text)
    text = _IMAGE_BADGE_PATTERN.sub("", text)

    # Clean trailing horizontal/whitespace clutter left by badge lines
    cleaned_lines = [line.rstrip() for line in text.splitlines()]
    text = "\n".join(cleaned_lines)

    text = _strip_boilerplate_sections(text)
    text = _strip_empty_headers(text)

    for token, rendered_block in fenced_blocks.items():
        text = text.replace(token, rendered_block)

    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()
