"""Package the Antigravity plugin directory into a version-stamped tarball and checksums."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import tarfile
import tempfile
from pathlib import Path

_VERSION_SAFE_CHARS = set("0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ.-+")


def normalize_version(raw_version: str) -> str:
    """Validate and normalize a version string (stripping an optional leading `v`)."""
    stripped = raw_version.strip()
    if stripped.lower().startswith("v"):
        stripped = stripped[1:]
    if not stripped or ".." in stripped or not set(stripped).issubset(_VERSION_SAFE_CHARS):
        raise ValueError(f"Invalid release version string: {raw_version!r}")
    return stripped


def package_plugin(
    plugin_dir: Path,
    output_dir: Path,
    version: str,
) -> Path:
    """Create `agymissioncontrol-plugin-v<version>.tar.gz` from `plugin_dir`."""
    resolved_plugin = plugin_dir.resolve()
    manifest_path = resolved_plugin / "plugin.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(f"Missing plugin manifest: {manifest_path}")

    manifest_data = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest_data, dict) or not manifest_data.get("name"):
        raise ValueError(f"Invalid plugin.json manifest in {manifest_path}")

    normalized_ver = normalize_version(version)
    output_dir.mkdir(parents=True, exist_ok=True)
    archive_path = output_dir.resolve() / f"agymissioncontrol-plugin-v{normalized_ver}.tar.gz"

    with tempfile.TemporaryDirectory() as tmp_dir:
        staged_dir = Path(tmp_dir) / "agymissioncontrol-plugin"
        shutil.copytree(
            resolved_plugin,
            staged_dir,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store"),
        )
        manifest_copy = dict(manifest_data)
        manifest_copy["version"] = normalized_ver
        (staged_dir / "plugin.json").write_text(
            json.dumps(manifest_copy, indent=2) + "\n",
            encoding="utf-8",
        )

        with tarfile.open(archive_path, "w:gz") as tar:
            for item in sorted(staged_dir.rglob("*")):
                rel = item.relative_to(Path(tmp_dir))
                tar.add(item, arcname=str(rel))

    return archive_path


def generate_checksums(directory: Path, output_filename: str = "checksums.txt") -> Path:
    """Compute SHA-256 digests (`sha256sum` format) for all artifacts in `directory`."""
    resolved_dir = directory.resolve()
    checksum_path = resolved_dir / output_filename
    lines: list[str] = []

    for artifact in sorted(resolved_dir.iterdir(), key=lambda p: p.name):
        if artifact.name == output_filename or not artifact.is_file():
            continue
        digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
        lines.append(f"{digest}  {artifact.name}")

    checksum_path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    return checksum_path


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint for `scripts/package_plugin.py`."""
    repo_root = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(description="Package Antigravity plugin release asset.")
    parser.add_argument("--version", required=True, help="Release version or tag (e.g. v0.2.0).")
    parser.add_argument(
        "--plugin-dir",
        type=Path,
        default=repo_root / "plugin",
        help="Path to source plugin directory.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=repo_root / "release_assets",
        help="Output directory for generated plugin tarball.",
    )
    parser.add_argument(
        "--checksums",
        action="store_true",
        help="Also generate SHA-256 checksums.txt in the output directory.",
    )
    args = parser.parse_args(argv)

    archive_path = package_plugin(args.plugin_dir, args.output, args.version)
    print(f"Packaged plugin archive: {archive_path}")
    if args.checksums:
        checksum_path = generate_checksums(args.output)
        print(f"Generated checksums: {checksum_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
