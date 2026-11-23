"""Atheris fuzz target for issue sanitization and TASKS.md manifest parsing."""

from __future__ import annotations

import sys
from contextlib import nullcontext

try:
    import atheris
except ImportError:  # pragma: no cover - local test fallback without atheris
    atheris = None  # type: ignore[assignment]

_instrument = atheris.instrument_imports() if atheris is not None else nullcontext()
with _instrument:
    from agymissioncontrol.manifest.parser import (
        parse_manifest_document,
        serialize_manifest,
    )
    from agymissioncontrol.manifest.sanitizer import sanitize_issue_body


def TestOneInput(data: bytes) -> None:
    """Exercise issue body sanitization and manifest AST round-tripping."""
    text = data.decode("utf-8", errors="replace")
    sanitized = sanitize_issue_body(text)
    if not isinstance(sanitized, str):
        raise AssertionError("sanitize_issue_body must return str")

    preamble, tasks = parse_manifest_document(text)
    serialized = serialize_manifest(tasks, header=preamble)
    if not isinstance(serialized, str):
        raise AssertionError("serialize_manifest must return str")


def main() -> None:
    """Run Atheris fuzzing loop."""
    if atheris is None:
        raise RuntimeError("atheris is required to run fuzz targets")
    atheris.Setup(sys.argv, TestOneInput)
    atheris.Fuzz()


if __name__ == "__main__":
    main()
