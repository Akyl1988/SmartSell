## [2026-01-06] Kaspi sync state last_error fields

### Added
- Persisted last_error_at/code/message on Kaspi sync failures with safe truncation and stable codes.
- Cleared last_error_* on success; state endpoint now returns persisted error metadata.
- Coverage for error persistence and clearing.

### Verified
- python -m pytest -q tests/app/api/test_kaspi_orders_sync.py

## [2026-01-06] Kaspi sync hardening: advisory lock + state endpoint

### Added
- Per-company Postgres advisory lock in Kaspi orders sync with fast-fail HTTP 423 to avoid concurrent runs.
- Request-scoped logging with request_id passthrough and duration metrics around sync.
- Read-only `/api/v1/kaspi/orders/sync/state` endpoint returning current watermark and error placeholders.
- Tests covering lock contention response and state endpoint defaults/watermark.

### Verified
- python -m ruff check app/api/v1/kaspi.py tests/app/api/test_kaspi_orders_sync.py
- python -m pytest -q tests/app/api/test_kaspi_orders_sync.py

## [2026-01-06] Fix Kaspi orders sync session usage

### Fixed
- Repaired `/api/v1/kaspi/orders/sync` to use the provided AsyncSession instead of undefined `db`, adding a safe transaction boundary (nested when pre-opened) and commit so inserts persist.
- Adjusted Kaspi service transaction handling to tolerate caller-managed sessions without double-opening transactions.

### Verified
- python -m ruff check app/api/v1/kaspi.py app/services/kaspi_service.py
- python -m pytest -q tests/app/api/test_kaspi_orders_sync.py

## [2026-01-04] Strip company_id inputs from v1

## [2026-01-06] Kaspi retry-after + idempotency

### Added
- Retry-After support with jitter for Kaspi order fetch retries to reduce thundering herd.
- Idempotency tests for Kaspi orders sync (duplicate runs, watermark progression, Retry-After handling).

### Verified
- python -m ruff check app/services/kaspi_service.py tests/app/api/test_kaspi_orders_sync.py
- python -m pytest -q tests/app/api/test_kaspi_orders_sync.py


### Added
- Guard coverage retained to detect any company_id Query/Path/Body/Field usage across v1 routes.

### Changed
- Removed all external company_id inputs from v1 APIs; subscriptions, invoices, wallet, payments, analytics, products, and Kaspi now scope strictly via resolve_tenant_company_id(current_user).

### Verified
- python -m ruff format app tests tools
- python -m ruff check app tests tools
- pytest -q tests/test_no_company_id_params_in_api_v1.py
- pytest -q

## [2026-01-04] Enforce tenant scoping across v1

### Added
- Regression coverage for invoice listing to ensure tenant admins are allowed only for their company and platform admins cannot override company_id.

### Changed
- Applied resolve_tenant_company_id scoping in analytics and products endpoints to remove implicit platform overrides.

### Verified
- python -m ruff format app tests tools
- python -m ruff check app tests tools
- pytest -q

## [2026-01-04] Tenant scoping: remove platform override for company_id

### Added
- Regression tests to block platform_admin from scoping wallet accounts and payments lists via foreign company_id while keeping tenant admins allowed.

### Changed
- Subscriptions list/current/create endpoints now ignore platform overrides and enforce company_id consistency with token scope.

### Verified
- python -m ruff format app tests tools
- python -m ruff check app tests tools
- pytest -q

## [2026-01-04] Tenant company scoping helper + query guardrails

### Added
- Shared tenant company resolver in pp/core/security.py to enforce company_id from auth claims and centralize platform-admin override rules.
- Regression tests covering company_id query behavior for wallet/payments (same-tenant allowed, cross-tenant forbidden) in 	ests/app/api/test_wallet_payments_tenant.py.

### Changed
- Wallet, payments, subscriptions, invoices, kaspi, and analytics endpoints now resolve company scope via the helper and reject mismatched query/body company_id values instead of trusting request parameters.

### Verified
- 
uff check app/core/security.py app/api/v1/payments.py app/api/v1/wallet.py app/api/v1/subscriptions.py app/api/v1/invoices.py app/api/v1/kaspi.py app/api/v1/analytics.py tests/app/api/test_wallet_payments_tenant.py
- pytest tests/app/api/test_wallet_payments_tenant.py -q
- pytest -q
## [2026-01-03] Tenant isolation: billing + wallet/payments; storage alignment

### Added
- Tenant-isolation tests for billing: `tests/app/test_tenant_isolation_billing.py`. [local]
- Tenant-scope API tests for wallet/payments: `tests/app/api/test_wallet_payments_tenant.py`. [local]

### Changed
- Wallet/Payments storages and API wiring aligned to tenant-scoped behavior (wallet/payments/campaigns sql storage adjustments). [32b6e1b, 69a5e40, 10afcf0]
- Request-scoped storage/session usage reinforced for wallet/payments to avoid cross-tenant leakage. [69a5e40]

### Fixed
- Stabilized tenant isolation behavior for billing + wallet/payments with coverage and guardrails. [32b6e1b, 69a5e40]

### Verified
- `python -m pytest -q tests/app/test_tenant_isolation_billing.py` → **4 passed**.
- `python -m pytest -q tests/app/api/test_wallet_payments_tenant.py` → **3 passed**.

### Notes / Follow-ups
- Keep tenant-scope pattern consistent in future wallet/payments/billing routes and storages; add tests first for any new query endpoints.

Commits (per git show):
- 69a5e40 fix(wallet/payments): safe nested tx + request-scoped storage; stabilize tests
- 10afcf0 fix(ci): unignore app/storage and commit wallet/payments storages
- 32b6e1b fix(billing): stabilize tenant tests; add safe_inspect for offline alembic; tenant-aware wallet listing

## [2025-12-31] CI
- tighten CI workflow: minimal ruff+pytest pipeline, fix invalid env contexts in Postgres service, set SECRET_KEY for tests, and keep SARIF upload optional with artifact retention
- CD gated to main with Docker push/login only when secrets exist; build still runs without secrets
- security workflow skips Code Scanning when disabled and guards uploads; release CI/CD merges finalized for v0.1.0

## [2025-12-31] CI/CD

### Added
- Новый job `alembic-smoke` в CI: быстрый smoke-тест миграций (`alembic upgrade head`, `alembic current`, `alembic heads`) на чистой Postgres 15 (GitHub Actions).
- Добавлен `.gitattributes` с правилами: `*.yml text eol=lf`, `*.yaml text eol=lf` (устранение CRLF-churn на Windows).

### Changed
- CD workflow (`cd.yml`):
  - Убраны все job-level if/выражения с `secrets.*` (валидно для GitHub Actions).
  - Секреты DockerHub теперь пробрасываются через job-level env.
  - Docker login и push выполняются только если оба секрета заданы; если нет — выполняется build-only (без push), чтобы CD не падал.

### Notes
- CI теперь гарантирует применимость всех миграций на чистую базу Postgres (smoke-проверка alembic).
- CD больше не ломается при отсутствии DockerHub secrets: всегда выполняется build, push — только если секреты заданы.

## [2025-12-31] Deps
- ensure passlib ships with argon2 backend in CI (add argon2-cffi and passlib[argon2])

## [2025-12-31] Migrations
- shorten Alembic revision id length to fit version_num column limits

## [2025-12-30] Docs
- document branching/release policy and add changelog with proper GitHub links

## [2025-12-30] Tests/Style
- resolve ruff pyupgrade warnings (isinstance unions) and fix conftest lint/UP038 issues

## [2025-12-29] Repo/DB
- enforce strict ruff+pytest gate (mypy soft-fail); clean legacy migration archives and ignore paths
- stabilize DB URL resolution and guard default DB usage; normalize drivers and debug route gating
## [2025-12-27] Merge integration center to dev/main
- merged: `feature/system-integration-center-v1` -> `dev`, then `dev` -> `main` (integration center v1, provider registry/configs, messaging webhook provider).
- commands: `alembic upgrade head`; `pytest -q`.
- results: `pytest -q` (137 passed, 5 skipped).
- warnings: Pydantic class-based `Config` deprecation, SQLAlchemy `Query.get` legacy, Trio `MultiError` deprecation, passlib/argon2 version warning.

## [2025-12-27] Pydantic v2 validator migration
- changed: migrated product schema validators (slug, sku, sale/max price checks, stock/galleries) and repricing config validator to `field_validator` to remove Pydantic v1 deprecation noise while preserving behavior.
- tests: `pytest -q` (137 passed, 5 skipped; warnings reduced to non-pydantic items: Config class deprecation, SQLAlchemy Query.get legacy, Trio/argon2).
- commands: `pytest -q`

## [2025-12-27] Integrations audit + admin RBAC
- changed: provider activation/healthcheck/config events now capture `actor_email`; admin endpoints forward user email for audit trail.
- tests: expanded `tests/test_admin_integrations.py` with non-admin access blocks and actor_email assertions; full suite `pytest -q` (133 passed, 5 skipped; warnings unchanged: Pydantic v1 validators, SQLAlchemy Query.get legacy, Trio deprecations, passlib/argon2 version warning).
- commands: `pytest -q`

## [2025-12-27] Messaging webhook provider
- added: webhook-based messaging provider with async httpx send + healthcheck, safe logging/redaction, retries, and encrypted configs via ProviderConfigService.
- changed: messaging resolver pulls encrypted configs, records config_missing/build_failed events, supports webhook provider; admin messaging convenience endpoints (list/config/healthcheck) forward actor_email in events.
- tests: new `tests/test_messaging_provider.py` covers config redaction, redis-down healthcheck resilience, hot-switch between noop/webhook, and actor_email in events; full suite `pytest -q` (137 passed, 5 skipped; warnings unchanged: Pydantic v1 validators, SQLAlchemy Query.get legacy, Trio deprecations, passlib/argon2 version warning).
- commands: `alembic upgrade head`; `pytest -q`

## [2025-12-27] Payments domain wiring
- added: payments port (healthcheck/create_payment_intent/refund + provider identity), NoOp payments gateway, PaymentProviderResolver with ProviderConfigService config/events/cache fallback, payments admin endpoints (list/config/healthcheck), DI alias `get_payment_service`
- changed: payment provider resolution fetches encrypted configs with events on missing/build errors; ProviderConfigService healthcheck supports payments; PaymentGateway keeps backward-compatible charge alias
- tests: added `tests/test_payments_provider.py`; full suite `pytest -q` (130 passed, 5 skipped; warnings unchanged)
- commands: `alembic upgrade head`; `pytest -q`

## [2025-12-27] Mobizon OTP provider
- added: Mobizon OTP provider (send/verify) with safe logging, retries/idempotency, and healthcheck; NoOp OTP provider now supports verify
- changed: OTP provider resolution pulls configs via ProviderConfigService with eventing and fallback to noop when config/build fails
- tests: added `tests/test_mobizon_provider.py`; full suite `pytest -q` (127 passed, 5 skipped; warnings unchanged)
- commands: `alembic upgrade head`; `pytest -q` (127 passed, 5 skipped)

## [2025-12-26] Admin Integrations: listing & events API
- Added: provider listing endpoint with filters + pagination (service layer + admin API).
- Added: events listing endpoint with filters (domain/provider/actor) + pagination; ordered results.
- Tests: extended tests/test_admin_integrations.py for listing + events filtering; pytest green (warnings only).
- Notes: существующие предупреждения остаются (Pydantic v1 @validator deprecations, SQLAlchemy Query.get legacy, Trio deprecations).

## [2025-12-26] OTP / Integrations
- added: runtime OTP provider resolution (OtpProviderResolver) with caching and safe fallback when registry/redis unavailable
- changed: OTP endpoints use resolver via DI (get_otp_service); hot-switch supported without restart
- tests: added test_otp_provider_hot_switch; alembic upgrade head OK; pytest -q OK (109 passed, 5 skipped)

## [2025-12-27] Provider resolvers + auth gating
- commands: `alembic heads`; `alembic upgrade head`; `pytest -q` (117 passed, 5 skipped; warnings persist: Pydantic v1 validators, SQLAlchemy Query.get, Trio deprecations, passlib/argon2 version warning)
- commits: `feat(otp): runtime provider resolver + hot-switch tests`; `security(auth): hide provider metadata in production behind DEBUG_PROVIDER_INFO`; `feat(integrations): messaging/payment resolvers + hot-switch tests`
- added: messaging/payment provider resolvers with caching + safe fallback, no-op providers enriched with metadata, hot-switch unit tests (`tests/test_provider_resolvers.py`)
- changed: auth OTP flow uses resolver DI and returns provider metadata gated by ENVIRONMENT/DEBUG_PROVIDER_INFO

## [2025-12-27] Integration Center configs
- commands: `alembic heads`; `alembic upgrade head`; `pytest -q` (121 passed, 5 skipped; warnings unchanged: Pydantic v1 validators, SQLAlchemy Query.get legacy, Trio deprecations, passlib/argon2)
- commits: `feat(db): provider config storage`; `feat(integrations): provider config management and healthcheck`
- added: `integration_provider_configs` table with encrypted payloads + key metadata; service-layer set/get/redaction/healthcheck; admin API endpoints for config read/write/healthcheck with idempotency and events; healthcheck resilient to redis failure; migration test added
- tests: config redaction/no secret leakage, healthcheck survives redis down, provider switch still works with resolver after config writes; alembic upgrade head smoke test

## [2025-12-31] Docs/env

### Added

### Changed
  - Минимальный и безопасный .env.example (только реально используемые переменные, без дублирования и unsafe значений).
  - Документация по переменным и запуску приведена к актуальному состоянию репозитория.
  - Все внешние ключи только как OPTIONAL с плейсхолдерами.

### Notes

## [2026-01-01] Release v0.1.1

### Added
- Tag v0.1.1 created from current main/dev (commit db3896b).
- GitHub Release v0.1.1 published with notes: env docs + CI Alembic smoke + CD gating.

### Notes
- v0.1.0 tag/release remains pointing to 72d114a (historical). We did not rewrite tags.
## [2026-01-01] Release v0.1.0

### Added
- GitHub Release: v0.1.0 (notes include CI stabilized + Alembic smoke + env docs).
- Tag v0.1.0 exists and is published.

### Notes
- main and dev are aligned and CI is green.


## [2026-01-03] Migrations + Tenant Isolation (Invoices/Subscriptions) + CI green

### Context
- Цель: устранить падение alembic offline/static SQL генерации (MockConnection) из-за инспекций и закрепить tenant isolation тестами для billing-сущностей.
- Ветка PR: feat/tenant-isolation-invoices-subscriptions → смержено в main (PR #20), dev приведён к main (FF).

### Changed
- Migrations:
  - `migrations/versions/20251228_subs_deleted_at.py` переписана на offline-safe DDL без инспекций (используются `IF EXISTS/IF NOT EXISTS`).
  - `migrations/versions/20260102_wallet_and_payments.py` устранены прямые `inspect(bind)` в пользу `safe_inspect(...)` или `None` в offline/mock сценариях.
  - CRLF-артефакты в миграциях нормализованы.
- API:
  - Добавлен `app/api/v1/invoices.py`.
  - Обновлён роутинг в `app/api/routes/__init__.py` для подключения invoices.
- Tests:
  - Добавлены tenant isolation тесты:
    - `tests/app/test_tenant_isolation_invoices.py`
    - `tests/app/test_tenant_isolation_subscriptions.py`
  - `tests/conftest.py` — корректировки под новые сценарии/фикстуры.

### Verification
- Clean tree: `git status` → clean.
- Ruff:
  - `python -m ruff format --check app tests tools` → OK
  - `python -m ruff check app tests tools` → OK
- Pytest:
  - `tests/test_migration_upgrade.py::test_alembic_upgrade_head_runs` → PASS
  - `tests/app/test_tenant_isolation_invoices.py` + `...subscriptions.py` → 4 PASS
- Alembic:
  - `python -m alembic heads` → single head: `20260102_wallet_and_payments`
  - `python -m alembic current` → `20260102_wallet_and_payments (head)`
- GitHub checks: all green (CI lint/tests/alembic smoke/security).

### Impact
- Offline/static SQL генерация Alembic больше не падает из-за инспекций.
- Tenant isolation для invoices/subscriptions зафиксирован тестами.
- main и dev синхронизированы (FF), feature-ветка удалена.

### Notes / Follow-ups
- Дальше: расширять tenant isolation на wallet/payments/billing сценарии и держать миграции offline-safe по умолчанию.
## [2026-01-03] Tenant scope: Wallet + Payments (storage+API) + expanded tests

### Context
- Закрываем tenant isolation для wallet/payments на уровне SQL storage + API.
- Ветка: feat/tenant-scope-wallet-payments.

### Changed
- Wallet:
  - Усилен tenant scoping в `app/storage/wallet_sql.py` и `app/api/v1/wallet.py` (account/ledger/deposit/withdraw/transfer).
- Payments:
  - Усилен tenant scoping в `app/storage/payments_sql.py` и `app/api/v1/payments.py` (create/refund/cancel/get/list + защита от query-param bypass).
- Tests:
  - `tests/app/api/test_wallet_payments_tenant.py` расширен до 10 кейсов (ledger, deposit/withdraw, transfer, create payment, cross-tenant + bypass guards).

### Verification
- `python -m ruff format --check app tests tools` → OK
- `python -m ruff check app tests tools` → OK
- `python -m pytest -q tests/app/api/test_wallet_payments_tenant.py` → 10 passed
- `python -m pytest -q tests/test_migration_upgrade.py::test_alembic_upgrade_head_runs` → 1 passed

### Impact
- Tenant isolation для wallet/payments закреплён в storage+API, тесты блокируют регрессии.

### Notes / Follow-ups
- Дальше: пройтись по остальным storage-слоям на паттерн “select by id без company constraint” и закрыть аналогично тестами.
## [2026-01-03] Tenant scope: Billing (Invoices + Subscriptions) + tests

### Changed
- Enforced tenant scoping in:
  - `app/api/v1/invoices.py` (list/get; rejects cross-tenant company_id bypass)
  - `app/api/v1/subscriptions.py` (scoped queries; rejects mismatched company_id; nested payments scoped)

### Tests
- Expanded billing tenant isolation coverage:
  - `tests/app/test_tenant_isolation_billing.py`
  - `tests/app/test_tenant_isolation_subscriptions.py`

### Verification
- `python -m ruff format --check app tests tools` → OK
- `python -m ruff check app tests tools` → OK
- `python -m pytest -q tests/app/test_tenant_isolation_billing.py` → OK
- `python -m pytest -q tests/app/test_tenant_isolation_subscriptions.py` → OK
- `python -m pytest -q` → OK
## [2026-01-03] Tenant scope: Campaigns (storage + API) + isolation tests

### Changed
- Enforced tenant scoping across campaigns flows:
  - `app/storage/campaigns_sql.py` (company-aware reads/writes and lookups)
  - `app/api/v1/campaigns.py` (company-scoped access; cross-tenant access returns 404)

### Tests
- Added tenant isolation regression coverage:
  - `tests/app/test_tenant_isolation_campaigns.py`

### Verification
- `python -m ruff format --check app tests tools` → OK
- `python -m ruff check app tests tools` → OK
- `python -m pytest -q tests/test_campaigns.py` → OK
- `python -m pytest -q tests/app/test_tenant_isolation_campaigns.py` → OK
- `python -m pytest -q tests/app/test_tenant_isolation.py -k "campaign"` → OK
## [2026-01-03] Tenant scope: Kaspi + Analytics + async-safe DB access (wallet/payments)

### Changed
- Enforced tenant scoping in analytics: all queries resolve effective company from authenticated user; foreign/missing company_id is rejected (403) where applicable.
- Kaspi endpoints: import order fixed and scoping safety improved for company/product operations.
- Wallet/Payments: hardened user loading and DB calls to be async-safe (AsyncSession-aware), avoiding sync `db.get()` usage on async paths.

### Verification
- `python -m ruff format --check app tests tools` → OK
- `python -m ruff check app tests tools` → OK
- `python -m pytest -q` → OK
## [2026-01-03] API hardening: async-only DB deps in v1 (campaigns) + guard test

### Changed
- `app/api/v1/campaigns.py`: switched DB dependency to `get_async_db` and removed sync `Session` path; user load now uses `await db.get(...)` only.

### Tests
- `tests/app/api/test_no_sync_db_calls.py`: guard test ensuring `db.get(` is always awaited in `app/api/v1/**`.

### Verification
- `python -m ruff check app tests tools` → OK
- `python -m pytest -q tests/app/api/test_no_sync_db_calls.py` → OK
### Also changed
- `app/api/v1/wallet.py`, `app/api/v1/payments.py`: removed sync `db.get()` usage; all `db.get(` calls are awaited (AsyncSession-only API).
## [2026-01-04] API v1: async-only DB deps

### changed
- Принудительно переведены все роутеры `app/api/v1/*` на `AsyncSession` и зависимость `get_async_db` (запрещён sync `get_db` в v1).
- Убраны остатки `sqlalchemy.orm.Session` (импорты/аннотации) из v1; для `run_sync` колбэков использован `Any`, чтобы не тащить `Session` в v1 слой.
- Приведены к async-стилю места с `commit/rollback/flush/refresh`, где это требовалось.

### fixed
- Почищены замечания ruff (в т.ч. `UP035`, `F401`, `B023`) после перехода на async-only.
- Усилен регрессионный тест: запрещает реальный `get_db`-dependency в `app/api/v1` и продолжает ловить не-awaited `db.get`.

### tests
- `python -m ruff format --check app tests tools`
- `python -m ruff check app tests tools`
- `python -m pytest -q` → **162 passed, 5 skipped**

### files
- `app/api/v1/auth.py`
- `app/api/v1/analytics.py`
- `app/api/v1/kaspi.py`
- `app/api/v1/products.py`
- `app/api/v1/users.py`
- `app/api/v1/wallet.py`
- `app/api/v1/payments.py`
- `tests/app/api/test_no_sync_db_calls.py`
## [2026-01-04] API v1 async-native: remove run_sync + stabilize wallet/payments

### changed
- Убраны `run_sync`/`session.query()` из `app/api/v1/*`; v1 слой полностью async-native (AsyncSession + select/execute/get).
- Guard-тесты усилены: запрещают `run_sync(` в `app/api/v1` и продолжают ловить не-awaited `db.get`.
- Wallet/Payments переведены на async-native путь без sync-сессий.

### fixed
- Исправлены падения tenant-isolation тестов по wallet/payments: добавлены wallet/payments таблицы в per-test cleanup, чтобы исключить “грязные хвосты” данных между тестами.

### tests
- `python -m ruff format app tests tools`
- `python -m ruff check app tests tools`
- `python -m pytest -q` → **163 passed, 5 skipped**

### files
- `app/api/v1/products.py`
- `app/api/v1/users.py`
- `app/api/v1/wallet.py`
- `app/api/v1/payments.py`
- `app/storage/wallet_sql.py`
- `app/storage/payments_sql.py`
- `tests/app/api/test_no_sync_db_calls.py`
- `tests/conftest.py`
## [2026-01-04] Tests teardown: interrupt-safe + engine dispose first

### changed
- Усилен teardown тестов: добавлен session-level guard на KeyboardInterrupt и общий disposer, чтобы sync/async engines закрывались первыми.
- При `PYTEST_KEEP_DB`/`KEEP_DB` и при прерывании тестов teardown пропускает alembic downgrade/drop и не роняет сессию; обычный прогон — best-effort cleanup.

### fixed
- Убраны нестабильные падения в конце прогона при `Ctrl+C`/долгом downgrade на Windows event loop.

### lint
- Приведён в порядок импорт-блок в миграциях:
  - `migrations/versions/20251228_subs_deleted_at.py`
  - `migrations/versions/20260102_wallet_and_payments.py`

### tests
- `python -m ruff check .`
- `python -m pytest -q` → **163 passed, 5 skipped**

### files
- `tests/conftest.py`
- `migrations/versions/20251228_subs_deleted_at.py`
- `migrations/versions/20260102_wallet_and_payments.py`
## [2026-01-04] Tests / RBAC

### fixed
- Устранён флейки-тест RBAC `tests/app/api/test_rbac_v2.py`: убрана зависимость от DB lookup по “магическому” телефону; проверка недостаточной роли теперь делается через вызов wallet endpoint и ожидание 403.

### notes
- Локальные проверки: `ruff check` и `pytest -q` — зелёные (167 passed, 5 skipped).

## [2026-01-04] Kaspi v1: orders sync without body + safe logging

### Changed
- `POST /api/v1/kaspi/orders/sync` no longer requires a request body (endpoint works with token-scoped tenant only).
- Removed the empty `OrdersSyncIn` model.
- Hardened error logging to avoid referencing undefined locals and to avoid logging request body; logs now use resolved company id.

### Added
- `tests/app/api/test_kaspi_endpoints.py`:
  - `/api/v1/kaspi/orders/sync` accepts empty requests and stays token-scoped.
  - `/api/v1/kaspi/feed` is token-scoped and ignores any company_id hints.

### Verified
- python -m ruff format --check app tests tools
- python -m ruff check app tests tools
- pytest -q
## [2026-01-04] Platform admin tenant access policy (v1)

### Changed
- Tenant-scoped v1 endpoints now consistently require tenant context from token; platform_admin/superadmin without company claim are denied (403) instead of any implicit fallback behavior.

### Added
- `tests/test_platform_admin_tenant_access_policy.py` to lock policy:
  - platform_admin without company_id claim gets 403 across tenant-scoped v1 endpoints (wallet/payments/invoices/subscriptions/products/analytics/kaspi).
  - tenant admin continues to receive 200.

### Verified
- python -m ruff format --check app tests tools
- python -m ruff check app tests tools
- pytest -q tests/test_platform_admin_tenant_access_policy.py
- pytest -q
## [2026-01-04] Kaspi v1 feed: remove silent fallback

### Fixed
- Removed `<feed/>` fallback on unexpected errors in `/api/v1/kaspi/feed`; endpoint now fails loudly (500) with safe exception logging to prevent masking integration failures.

### Added
- Regression test: feed returns 500 when service raises unexpected exception.
## [2026-01-05] Kaspi Orders Sync MVP: sync state model skeleton

### Added
- `app/models/kaspi_order_sync_state.py`: persistent sync watermark/state for Kaspi orders sync.
- Export entry in `app/models/__init__.py`.

### Changed
- Minimal prep in `app/models/order.py` (Kaspi-related metadata marker).
- `kaspi_service.get_orders` now preserves provided date_from/date_to + status filters (no behavioral changes beyond request param handling).

### Verified
- ruff format/check (app/tests/tools)
## [2026-01-06] DB: deterministic async DB URL resolution (fix InvalidPasswordError in runtime)
- fixed: async engine could select a different URL than migrations/psql and lose password, causing InvalidPasswordError
- added: resolve_async_database_url() with strict priority (TEST_ASYNC_DATABASE_URL > TEST_DATABASE_URL > fallback) + scheme normalization to postgresql+asyncpg
- added: password injection when missing (DB_PASSWORD -> PGPASSWORD -> borrow from DATABASE_URL/DB_URL), without logging secrets
- updated: async engine init now uses async resolver and logs safe debug-only diagnostics
- tests: test_db_async_url_resolution.py + kept pgpass/password fallback coverage
## [2026-01-06] Kaspi: orders sync MVP (incremental + idempotent)
- added: incremental sync using kaspi_order_sync_state watermark with 2-minute overlap for safety
- added: idempotent upsert for orders via unique (company_id, external_id)
- added: per-company concurrency guard via pg_try_advisory_xact_lock
- api: /api/v1/kaspi/orders/sync returns 409 when sync is already running
- tests: standardized async test marks to pytest.mark.asyncio; conftest cleanup and fixture compatibility
## [2026-01-06] CI: ruff UP017 fix
- fixed: ruff UP017 (use datetime.UTC alias) in kaspi orders sync tests; formatting aligned with CI
## [2026-01-06] Git: restore dev branch after accidental deletion
- fixed: restored remote/local dev branch from main after gh pr merge --delete-branch removed dev
- notes: protect dev/main branches (disable deletions) to prevent recurrence
## [2026-01-06] Kaspi: idempotent order items
- added: unique constraint order_items(order_id, sku) + migration 2d43c3d56e28_kaspi_unique_order_items_order_id_sku
- changed: Kaspi orders sync now upserts OrderItem by (order_id, sku) to prevent duplicates; item fields update on conflict
- tests: extended kaspi orders sync tests to cover item idempotency
## [2026-01-06] Kaspi: order status refresh + idempotent status history
- added: unique constraint for OrderStatusHistory to prevent duplicates by (order_id, status, changed_at) + migration 29a2929fc59b_kaspi_order_status_history_unique
- changed: Kaspi orders sync refreshes Order status/updated_at from payload timestamps and records status history idempotently (ON CONFLICT DO NOTHING)
- tests: extended kaspi orders sync tests to cover status updates + non-duplicating status history
