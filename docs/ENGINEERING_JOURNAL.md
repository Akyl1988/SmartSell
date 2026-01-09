# Engineering Journal

## 2025-12-29
- Added `scripts/prod-gate.ps1` automated prod-gate pipeline (pip check, ruff, mypy, pytest, alembic, uvicorn smoke, fail-fast guard, gitleaks, docker smoke) with fail-fast behavior and masking of DSN secrets.
- Documented usage and troubleshooting in `docs/PROD_GATE.md`.
- CI workflow aligned to prod-gate stages.

## [2026-01-09] Kaspi orders sync MVP coverage

### Added
- Added MVP test suite for Kaspi orders sync: idempotency, watermark advancement/filtering, upsert updates, advisory lock (423), and error persistence.
- Added KASPI_SYNC_MVP_SUMMARY.md documenting verified behavior and test results.

### Verified
- python -m ruff format tests/app/api/test_kaspi_orders_sync_mvp.py
- python -m pytest tests/app/api/test_kaspi_orders_sync_mvp.py -q
- python -m pytest tests/ -q

