"""Atheris fuzz target for the Antigravity PreToolUse command guard."""

from __future__ import annotations

import sys
from contextlib import nullcontext
from pathlib import Path

try:
    import atheris
except ImportError:  # pragma: no cover - local test fallback without atheris
    atheris = None  # type: ignore[assignment]

_PLUGIN_SCRIPTS = Path(__file__).resolve().parent.parent / "plugin" / "scripts"
if _PLUGIN_SCRIPTS.is_dir() and str(_PLUGIN_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_SCRIPTS))

_instrument = atheris.instrument_imports() if atheris is not None else nullcontext()
with _instrument:
    from command_guard import evaluate_command


def TestOneInput(data: bytes) -> None:
    """Exercise ``evaluate_command`` against arbitrary byte sequences."""
    command = data.decode("utf-8", errors="replace")
    decision, reason = evaluate_command(command)
    if decision not in {"allow", "ask", "deny"}:
        raise AssertionError(f"Unexpected decision: {decision!r}")
    if not isinstance(reason, str):
        raise AssertionError(f"Unexpected reason type: {type(reason)!r}")


def main() -> None:
    """Run Atheris fuzzing loop."""
    if atheris is None:
        raise RuntimeError("atheris is required to run fuzz targets")
    atheris.Setup(sys.argv, TestOneInput)
    atheris.Fuzz()


if __name__ == "__main__":
    main()
