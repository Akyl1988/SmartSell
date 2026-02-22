from __future__ import annotations

from pathlib import Path


def test_kaspi_feed_upload_uses_application_xml() -> None:
    script_path = Path(__file__).resolve().parents[2] / "scripts" / "Kaspi.ps1"
    content = script_path.read_text(encoding="utf-8")
    assert "application/xml" in content
