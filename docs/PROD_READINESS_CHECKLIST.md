# Prod Readiness Checklist

## P0 (must before prod)
- [ ] SECRET_KEY and DB URLs set via secrets manager; no weak defaults (e.g., `changeme`, vendor factory passwords).
- [ ] Disable or protect `/api/v1/_debug/db` behind auth/feature flag in production.
- [ ] Confirm Alembic head (`alembic heads`) single; remove backup/quarantine migrations; set secure `ALEMBIC_DEFAULT_URL`.
- [ ] Backups and rollback plan documented for DB migrations.
- [ ] Align installed deps to pinned set (fastapi/starlette/anyio/trio) and add lockfile; reinstall env.
- [x] Enforce single active/trial/overdue/paused subscription per company (partial unique index `uq_subscription_company_active_states`, rev `20251228_active_subscription_uniqueness`).

## P1
- [ ] Pin dependency versions in lockfile; document upgrade path for FastAPI/Starlette/AnyIO/Trio.
- [ ] Add CI steps: `pip check`, `pytest -q`, `alembic upgrade head --sql` dry-run.
- [ ] Address Pydantic config deprecation warnings; update models/config accordingly.
- [ ] Document asyncio-only support or add trio backend coverage; monitor asyncpg compatibility.
- [ ] Consolidate httpx version (runtime vs dev) to avoid drift.
- [x] SMTP in TESTING/CI fixed to port 587 (ignores `.env` overrides); add `SMTP_PORT_TEST` to override safely if needed.

## P2
- [ ] Implement missing ТЗ features (TipTop Pay, analytics, AI bot, full Kaspi workflows) and align frontend.
- [ ] Harden rate limiting and audit logging; verify security headers.
- [ ] Add observability for integrations (metrics, structured logs).
- [ ] Perform load test and basic SLO definition (latency, error budget).
