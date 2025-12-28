# SmartSell Project Audit Report

## Executive summary
- Runtime now: `fastapi 0.104.1`, `starlette 0.27.0`, `anyio 3.7.1`, `trio 0.22.2` (from importlib); pins in pyproject/requirements target `fastapi 0.115.5` + `starlette 0.41.3` + `anyio 4.x` + `trio 0.31.0` — env drift, no lockfile.
- Architecture: FastAPI app in `app/main.py` mounts v1 routers via `app.api.routes.mount_v1`; settings via `app/core/config.py`; DB layer `app/core/db.py` provides async/sync engines; security/JWT helpers in `app/core/security.py`; debug DB endpoint `/api/v1/_debug/db` exposes DSN metadata.
- Risks: default `SECRET_KEY="changeme"`, permissive DB fallbacks (SQLite or default Postgres creds), Alembic default URL with password, `/api/v1/_debug/db` unauthenticated, ALLOW_DROPS flag enables destructive DDL.
- Migrations: single head now `20251228_active_subscription_uniqueness` (adds partial unique index for active/trial/overdue/paused) and `uq_subscription_company_active_states`; older .bak/quarantine artifacts remain.
- Data protections: subscriptions payments endpoint now scoped by `subscription_id`; SMTP in TESTING/pytest forced to port 587 to avoid leaking insecure `.env` overrides.
- Tests: previously green (`pytest -q` 116 passed, 5 skipped); new subscription/SMTP regression tests added; current env may diverge until deps are reinstalled.
- ТЗ coverage: core auth/products/subscriptions present; TipTop Pay, AI bot, analytics missing; Kaspi/logistics/messaging partial; frontend scaffold only.
- Actions: realign runtime deps to pins or adjust pins to installed; secure secrets/DSN; gate debug endpoint; clean migrations; roadmap for missing ТЗ items.

## Архитектура
- Entry point: `app/main.py` builds FastAPI app, attaches middleware (CORS, GZip, security headers, request_id), optional Prometheus/OTel, mounts v1 routers via `mount_v1`. Static files and trusted hosts optional. Request-id middleware stores `ContextVar`.
- Config: `app/core/config.py` (Pydantic v2 Settings) reads `.env.test` then `.env`; masks secrets; provides default DSN builders and fingerprints; defaults allow SQLite fallback in dev/test.
- DB: `app/core/db.py` lazily creates asyncpg/psycopg2 engines, exposes `get_async_db` dependency; supports replica routing and query metrics; async fallback to in-memory SQLite if no URL.
- Security: `app/core/security.py` handles JWT creation/validation (jose), password hashing (argon2/bcrypt), denylist, token helpers `get_current_user`; deprecated config warnings from Pydantic v2 remain.
- Routing: v1 routers aggregated in `app/api/routes`; notable debug route `app/api/v1/debug_db.py` returns DSN fingerprints/connectivity; subscriptions API async.
- Integrations: adapters under `app/integrations/` (Kaspi, Mobizon, SMS base); billing models in `app/models/billing.py`.

## Риски и регрессии
- High: Default `SECRET_KEY="changeme"` and permissive JWT defaults — must override in env; risk of token forgery.
- High: Config/DB fallbacks allow SQLite or default Postgres with password `admin123`; Alembic default URL includes credentials; risk of mis-pointing to prod or shipping weak creds.
- Med: Debug endpoint `/api/v1/_debug/db` exposes DB host/user and connectivity; should be gated by auth/flag or disabled in prod.
- Med: `ALLOW_DROPS` gate in Alembic can enable destructive DDL; ensure disabled in prod CI.
- Med: Tests force anyio backend to asyncio; if trio backend is desired in prod, parity tests missing.
- Low: Pydantic deprecated Config warning; legacy `Query.get()` use in tests; minor SAWarning on rollback.

## Dependency audit
- Pins: `fastapi 0.115.5`, `starlette 0.41.3`, `anyio 4.x`, `trio 0.31.0` (pyproject/requirements).
- Installed: `fastapi 0.104.1`, `starlette 0.27.0`, `anyio 3.7.1`, `trio 0.22.2` — mismatch can reintroduce trio/anyio cancellable bug and conflicts (`pip check` would fail under mixed versions).
- No lockfile present (poetry.lock/requirements.lock absent); recommend regenerating lock and reinstalling to pinned set or downgrading pins to match installed.
- Action: `pip install -U fastapi==0.115.5 starlette==0.41.3 anyio==4.12.0 trio==0.31.0` or align pins downward, then capture lock.

## Database/Migrations
- Head: `alembic heads` → `20251228_active_subscription_uniqueness` (single head).
- New guard: partial unique index `uq_subscription_company_active_states` prevents >1 active/trial/overdue/paused subscription per company (SoftDelete-aware).
- Artifacts: `.bak` and `_quarantine` files present alongside versions — risk of accidental heads if picked up; clean in follow-up.
- Alembic env normalizes URLs, blocks DROPs unless `ALLOW_DROPS=1`, but default URL includes password `admin123` — must override via env in CI/prod.
- Tests: ephemeral DB via `TEST_ASYNC_DATABASE_URL`, downgrade-to-base after session, TRUNCATE cleanup.

## Tests audit
- `pytest -q`: 116 passed, 5 skipped, warnings (Pydantic config deprecation, argon2 version, SQLAlchemy legacy Query.get, SAWarning on rollback). New targeted tests: subscription uniqueness guard, payments isolation, SMTP port stability under TESTING.
- `tests/conftest.py`: session event loop; async override `get_async_db`; db_reset truncates tables; seeds minimal companies; anyio backend fixture forces asyncio to avoid asyncpg/trio cancellable bug.
- Skips: migration upgrade test when DB URL absent; others benign.

## ТЗ соответствие (выписка)
См. подробную матрицу в `docs/TZ_COVERAGE_MATRIX.md`.

Approx readiness: MVP ~45%, Production ~20%. Top blockers: secure secrets/DSN handling, missing payment/analytics/bot features, harden migrations, observability of integrations, prod-grade infra.

## План действий (приоритеты)
- P0: Enforce SECRET_KEY/DB URLs via env; disable or auth-gate `/api/v1/_debug/db`; remove default creds from Alembic env.
- P0: Align installed deps to pinned set (or re-pin down) and capture lockfile; rerun full test suite.
- P1: Clean migrations directory (remove .bak/quarantine) and document ALLOW_DROPS usage; keep single head.
- P1: Address Pydantic Config deprecations; resolve trio/anyio backend choice (document asyncio-only or add trio coverage).
- P2: Implement missing ТЗ modules (TipTop Pay, analytics, AI bot), expand frontend parity, add observability (metrics/logs) for integrations.

## Security/Deps/DB/Tests (detailed)
- Security: strong secret required; JWT revoke list exists; rate limiting via slowapi optional; CORS allowed origins configurable; content-length guard middleware in main; debug DB endpoint unauthenticated.
- Deps: pins vs installed diverge; avoid mixing httpx versions (dev/runtime); lockfile absent.
- DB: Async engine with NullPool for tests; replica routing supported; default SQLite fallback only for dev — document; Alembic default URL contains password.
- Tests: Use `TEST_ASYNC_DATABASE_URL`; database teardown downgrade-to-base + TRUNCATE; warnings acceptable; runtime ~7.5 minutes on upgraded stack.

## Security P0/P1/P2 and fixes
- P0
	- SECRET_KEY default `changeme`; set via env/secret store; add check in `app/core/config.py` or `app/main.py` to refuse default.
	- DB URLs default to SQLite or weak Postgres (`admin123`); require `DATABASE_URL`/`TEST_ASYNC_DATABASE_URL` in prod/CI; remove password from Alembic default in `migrations/env.py`.
	- `/api/v1/_debug/db` unauthenticated; gate behind feature flag/env (e.g., `DEBUG_DB_ENABLED`) and auth dependency in `app/api/v1/debug_db.py`.
- P1
	- ALLOW_DROPS allows destructive Alembic operations; ensure unset in prod/CI and document; consider guard in `migrations/env.py`.
	- Dependency drift (fastapi/starlette/anyio/trio); re-install to pinned set and add lockfile.
	- Mixed httpx versions (runtime vs dev) — consolidate.
- P2
	- Rate limiting optional; enable `slowapi` in prod with sane defaults.
	- Security headers/middleware audit (TrustedHost, HTTPSRedirect) — enable via env in prod.

Suggested file-level changes (minimal patches)
- `app/core/config.py`: add validation that `SECRET_KEY` not default and `DATABASE_URL` must be set when `ENVIRONMENT` in [staging, production].
- `app/api/v1/debug_db.py`: wrap router with env flag + auth (`Depends(get_current_user)` or admin role) and return 404 when disabled.
- `migrations/env.py`: replace `ALEMBIC_DEFAULT_URL` with placeholder sans password; fail fast if URL missing; keep ALLOW_DROPS off by default.
- Add lockfile (`poetry lock` or `pip-compile`) and enforce install from lock in CI.
