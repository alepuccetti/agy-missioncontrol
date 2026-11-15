"""Tests verifying the compiled Quota Cockpit static frontend bundle."""

from __future__ import annotations

from pathlib import Path

STATIC_DIR = Path(__file__).resolve().parent.parent / "src" / "agymissioncontrol" / "ui" / "static"


def _read_bundle_texts() -> tuple[str, str, str]:
    index_html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    js_chunks = "\n".join(
        p.read_text(encoding="utf-8") for p in sorted((STATIC_DIR / "assets").glob("*.js"))
    )
    css_chunks = "\n".join(
        p.read_text(encoding="utf-8") for p in sorted((STATIC_DIR / "assets").glob("*.css"))
    )
    return index_html, js_chunks, css_chunks


def test_compiled_static_bundle_exists_and_assets_present() -> None:
    index_path = STATIC_DIR / "index.html"
    assets_dir = STATIC_DIR / "assets"
    assert index_path.is_file()
    assert index_path.stat().st_size > 0
    assert assets_dir.is_dir()
    assert list(assets_dir.glob("*.js")), "Expected compiled Vite JS bundle in static/assets/"
    assert list(assets_dir.glob("*.css")), "Expected compiled Vite CSS bundle in static/assets/"


def test_static_bundle_contains_alpine_endpoints_chart_and_theme() -> None:
    index_html, js_chunks, css_chunks = _read_bundle_texts()

    assert 'x-data="quotaCockpit"' in index_html
    assert "trend-chart" in index_html
    assert "Wake All" in index_html
    assert "Stop All" in index_html
    assert "Refresh Now" in index_html

    for endpoint in (
        "/api/quota/status",
        "/api/quota/history",
        "/api/sessions",
        "/api/locks",
    ):
        assert endpoint in index_html
        assert endpoint in js_chunks

    for mutation_endpoint in (
        "/api/wake/",
        "/api/wake-all",
        "/api/stop/",
        "/api/stop-all",
        "/api/locks/clear/",
    ):
        assert mutation_endpoint in js_chunks

    assert "agymissioncontrol_auto_refresh" in js_chunks
    assert "15000" in js_chunks or "15e3" in js_chunks

    for color_hex in ("#0f1117", "#1a1d27", "#2d3139"):
        assert color_hex in css_chunks.lower()
