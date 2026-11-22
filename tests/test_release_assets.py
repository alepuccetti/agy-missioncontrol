"""Tests for E03-T05 release pipeline and plugin packaging utilities."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
PACKAGE_SCRIPT = REPO_ROOT / "scripts" / "package_plugin.py"
RELEASE_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "release.yml"


def _load_package_plugin_module() -> Any:
    """Import `scripts/package_plugin.py` directly by file path."""
    spec = importlib.util.spec_from_file_location("package_plugin", PACKAGE_SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_normalize_version_accepts_and_strips_prefix() -> None:
    """`normalize_version` strips optional `v`/`V` prefix and preserves semver metadata."""
    mod = _load_package_plugin_module()

    assert mod.normalize_version("v0.2.0-beta") == "0.2.0-beta"
    assert mod.normalize_version("V1.2.3+build.45") == "1.2.3+build.45"
    assert mod.normalize_version("  0.2.0  ") == "0.2.0"


@pytest.mark.parametrize(
    "bad_version",
    ["", "v", "  ", "../1.0.0", "1.0.0/extra", "1.0.0 beta", "v1.0..2"],
)
def test_normalize_version_rejects_invalid_strings(bad_version: str) -> None:
    """`normalize_version` rejects empty, traversal, whitespace, or invalid strings."""
    mod = _load_package_plugin_module()

    with pytest.raises(ValueError, match="Invalid release version"):
        mod.normalize_version(bad_version)


def test_package_plugin_stamps_version_in_copy_and_skips_junk(tmp_path: Path) -> None:
    """Plugin tarball stamps `version` without mutating source and excludes junk files."""
    mod = _load_package_plugin_module()

    src_plugin = tmp_path / "src_plugin"
    (src_plugin / "skills" / "demo").mkdir(parents=True)
    (src_plugin / "__pycache__").mkdir()
    manifest_payload = {"name": "demo-plugin", "description": "Demo plugin"}
    manifest_path = src_plugin / "plugin.json"
    manifest_path.write_text(json.dumps(manifest_payload, indent=2) + "\n", encoding="utf-8")
    (src_plugin / "skills" / "demo" / "SKILL.md").write_text("# Demo Skill\n", encoding="utf-8")
    (src_plugin / "__pycache__" / "mod.cpython-315.pyc").write_bytes(b"pyc-bytes")
    (src_plugin / ".DS_Store").write_bytes(b"ds-store")

    out_dir = tmp_path / "out"
    archive_path = mod.package_plugin(src_plugin, out_dir, "v0.2.0-beta")

    assert archive_path == (out_dir / "agymissioncontrol-plugin-v0.2.0-beta.tar.gz").resolve()
    assert archive_path.is_file()
    # Original manifest remains clean without injected version.
    assert json.loads(manifest_path.read_text(encoding="utf-8")) == manifest_payload

    with tarfile.open(archive_path, "r:gz") as tar:
        names = tar.getnames()
        assert "agymissioncontrol-plugin/plugin.json" in names
        assert "agymissioncontrol-plugin/skills/demo/SKILL.md" in names
        assert not any("__pycache__" in name or name.endswith(".pyc") for name in names)
        assert not any(name.endswith(".DS_Store") for name in names)

        member = tar.extractfile("agymissioncontrol-plugin/plugin.json")
        assert member is not None
        packaged_manifest = json.loads(member.read().decode("utf-8"))
        assert packaged_manifest["name"] == "demo-plugin"
        assert packaged_manifest["version"] == "0.2.0-beta"


def test_package_plugin_validates_manifest(tmp_path: Path) -> None:
    """`package_plugin` raises clear errors when `plugin.json` is missing or invalid."""
    mod = _load_package_plugin_module()
    plugin_dir = tmp_path / "bad_plugin"
    plugin_dir.mkdir()
    out_dir = tmp_path / "out"

    with pytest.raises(FileNotFoundError):
        mod.package_plugin(plugin_dir, out_dir, "0.2.0")

    (plugin_dir / "plugin.json").write_text('{"description": "missing name"}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="Invalid plugin.json"):
        mod.package_plugin(plugin_dir, out_dir, "0.2.0")


def test_repo_plugin_dir_packages_cleanly_via_cli(tmp_path: Path) -> None:
    """Running `scripts/package_plugin.py` packages repo `plugin/` and writes `checksums.txt`."""
    repo_manifest = REPO_ROOT / "plugin" / "plugin.json"
    original_manifest_bytes = repo_manifest.read_bytes()

    result = subprocess.run(
        [
            sys.executable,
            str(PACKAGE_SCRIPT),
            "--version",
            "v0.2.0-beta",
            "--output",
            str(tmp_path),
            "--checksums",
        ],
        capture_output=True,
        text=True,
        check=True,
    )

    assert "Packaged plugin archive:" in result.stdout
    assert "Generated checksums:" in result.stdout

    archive = tmp_path / "agymissioncontrol-plugin-v0.2.0-beta.tar.gz"
    checksums = tmp_path / "checksums.txt"
    assert archive.is_file()
    assert checksums.is_file()
    # Repo plugin manifest must remain untouched (no git dirt).
    assert repo_manifest.read_bytes() == original_manifest_bytes

    expected_hash = hashlib.sha256(archive.read_bytes()).hexdigest()
    assert checksums.read_text(encoding="utf-8").strip() == f"{expected_hash}  {archive.name}"


def test_generate_checksums_sorted_and_excludes_self_and_dirs(tmp_path: Path) -> None:
    """`generate_checksums` emits sorted `sha256sum` lines and ignores `checksums.txt`/dirs."""
    mod = _load_package_plugin_module()

    file_b = tmp_path / "agymissioncontrol-linux-x86_64"
    file_a = tmp_path / "agymissioncontrol-darwin-arm64"
    file_b.write_bytes(b"linux-binary")
    file_a.write_bytes(b"darwin-binary")
    (tmp_path / "nested_dir").mkdir()
    (tmp_path / "checksums.txt").write_text("stale content\n", encoding="utf-8")

    out = mod.generate_checksums(tmp_path)
    lines = out.read_text(encoding="utf-8").splitlines()

    assert out == (tmp_path / "checksums.txt").resolve()
    assert len(lines) == 2
    assert lines[0] == f"{hashlib.sha256(b'darwin-binary').hexdigest()}  {file_a.name}"
    assert lines[1] == f"{hashlib.sha256(b'linux-binary').hexdigest()}  {file_b.name}"


def test_release_workflow_uses_pinned_runners_and_required_steps() -> None:
    """Verify `.github/workflows/release.yml` pins LTS runners and executes required stages."""
    raw_text = RELEASE_WORKFLOW.read_text(encoding="utf-8")
    assert "-latest" not in raw_text, "Floating *-latest runner labels are prohibited."

    workflow = yaml.safe_load(raw_text)
    assert isinstance(workflow, dict)

    # PyYAML parses unquoted `on` as boolean True per YAML 1.1 rules.
    trigger = workflow[True] if True in workflow else workflow["on"]
    assert trigger["push"]["tags"] == ["v*.*.*"]
    assert workflow["permissions"]["contents"] == "read"

    jobs = workflow["jobs"]
    build_job = jobs["build"]
    publish_job = jobs["publish"]

    assert build_job["permissions"]["contents"] == "read"
    assert publish_job["permissions"] == {
        "contents": "write",
        "id-token": "write",
        "attestations": "write",
    }

    assert build_job["runs-on"] == "${{ matrix.os }}"
    matrix_entries = build_job["strategy"]["matrix"]["include"]
    assert matrix_entries == [
        {"os": "macos-15", "artifact_name": "agymissioncontrol-darwin-arm64"},
        {"os": "ubuntu-24.04", "artifact_name": "agymissioncontrol-linux-x86_64"},
    ]

    build_script = "\n".join(str(step.get("run", "")) for step in build_job["steps"])
    assert "src/agymissioncontrol/ui/frontend/package.json" in build_script
    assert "task ui:install" in build_script
    assert "task ui:build" in build_script
    assert "task build:binary" in build_script
    assert "./dist/agymissioncontrol --version" in build_script
    assert "./dist/agymissioncontrol status --mock" in build_script

    assert publish_job["needs"] == "build"
    assert publish_job["runs-on"] == "ubuntu-24.04"
    publish_script = "\n".join(str(step.get("run", "")) for step in publish_job["steps"])
    assert "scripts/package_plugin.py" in publish_script
    assert "sha256sum * > checksums.txt" in publish_script
    assert "gh release create" in publish_script


def test_package_plugin_main_copies_clean_archive_into_empty_dir(tmp_path: Path) -> None:
    """`package_plugin.main()` handles custom `--plugin-dir` and empty directory gracefully."""
    mod = _load_package_plugin_module()
    empty_dir = tmp_path / "empty_checksums"
    empty_dir.mkdir()
    checksum_file = mod.generate_checksums(empty_dir)
    assert checksum_file.read_text(encoding="utf-8") == ""

    copied_plugin = tmp_path / "plugin_copy"
    shutil.copytree(REPO_ROOT / "plugin", copied_plugin)
    out_dir = tmp_path / "release_out"
    rc = mod.main(
        [
            "--version",
            "0.2.0-beta",
            "--plugin-dir",
            str(copied_plugin),
            "--output",
            str(out_dir),
            "--checksums",
        ]
    )
    assert rc == 0
    assert (out_dir / "agymissioncontrol-plugin-v0.2.0-beta.tar.gz").is_file()
    assert (out_dir / "checksums.txt").is_file()
