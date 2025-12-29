# Engineering Journal

## 2025-12-29
- Added `scripts/prod-gate.ps1` automated prod-gate pipeline (pip check, ruff, mypy, pytest, alembic, uvicorn smoke, fail-fast guard, gitleaks, docker smoke) with fail-fast behavior and masking of DSN secrets.
- Documented usage and troubleshooting in `docs/PROD_GATE.md`.
- CI workflow aligned to prod-gate stages.
