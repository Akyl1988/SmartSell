from __future__ import annotations

from pathlib import Path


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_env_example_prod_has_required_keys():
    content = _read(Path(".env.example.prod"))
    assert "ENVIRONMENT=production" in content
    assert "SECRET_KEY=" in content


def test_runbooks_reference_release_gate_and_health():
    deploy = _read(Path("docs/runbooks/deploy_prod.md"))
    release = _read(Path("docs/runbooks/release_process.md"))

    assert "prod-gate.ps1" in release
    assert "/api/v1/wallet/health" in deploy
    assert "/openapi.json" in deploy
