"""Contract test for deployment guide."""

from __future__ import annotations

from pathlib import Path


def test_deployment_guide_exists_and_has_key_anchors() -> None:
    root = Path(__file__).resolve().parents[1]
    guide = root / "docs" / "DEPLOYMENT.md"

    assert guide.exists(), "docs/DEPLOYMENT.md must exist"

    content = guide.read_text(encoding="utf-8").lower()

    required_terms = [
        "alembic upgrade head",
        "database_url",
        "secret_key",
        "pgcrypto_key",
        "integrations_master_key",
        "/api/v1/health",
    ]

    for term in required_terms:
        assert term in content, f"missing required term: {term}"

    assert ("gunicorn" in content) or ("uvicorn" in content)
    assert ("nginx" in content) or ("reverse proxy" in content)
