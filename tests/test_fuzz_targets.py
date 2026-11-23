"""Tests for ClusterFuzzLite fuzz targets, config, and workflow."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_command_guard_fuzzer_handles_edge_inputs() -> None:
    mod = _load_module(REPO_ROOT / "fuzz" / "command_guard_fuzzer.py", "command_guard_fuzzer")
    samples = [
        b"",
        b"\xff\xfe\xfd",
        b"rm -rf /",
        b"git status && task check | head -n 5",
        b'echo "unclosed quote',
        b"$(whoami) `id` ; sudo env FOO=1 rm -rf ~/",
        b"a" * 4096,
    ]
    for sample in samples:
        mod.TestOneInput(sample)


def test_manifest_parser_fuzzer_handles_edge_inputs() -> None:
    mod = _load_module(REPO_ROOT / "fuzz" / "manifest_parser_fuzzer.py", "manifest_parser_fuzzer")
    samples = [
        b"",
        b"\xff\xfe\xfd",
        b"# Header\n<!-- comment -->\n[![badge](https://img.shields.io/a)](https://b)\n",
        (
            b"- [ ] **T-1: Build fuzzer** (Issue #1)\n"
            b"  - Depends-On: `T-0`\n"
            b"  - Acceptance Criteria:\n"
            b"    - [x] Runs\n"
        ),
        b"```log\n" + (b"line\n" * 60) + b"```\n",
    ]
    for sample in samples:
        mod.TestOneInput(sample)


def test_clusterfuzzlite_files_and_workflow_structure() -> None:
    cflite_dir = REPO_ROOT / ".clusterfuzzlite"
    dockerfile_text = (cflite_dir / "Dockerfile").read_text(encoding="utf-8")
    assert "base-builder-python" in dockerfile_text
    assert "build.sh $SRC/" in dockerfile_text
    assert (cflite_dir / "build.sh").is_file()

    project_cfg = yaml.safe_load((cflite_dir / "project.yaml").read_text(encoding="utf-8"))
    assert project_cfg["language"] == "python"
    assert "address" in project_cfg["sanitizers"]

    workflow = yaml.safe_load(
        (REPO_ROOT / ".github" / "workflows" / "cflite_pr.yml").read_text(encoding="utf-8")
    )
    assert workflow["permissions"]["contents"] == "read"
    fuzz_job = workflow["jobs"]["fuzz"]
    assert fuzz_job["runs-on"] == "ubuntu-24.04"
    steps = fuzz_job["steps"]
    assert all(step["with"]["language"] == "python" for step in steps)
