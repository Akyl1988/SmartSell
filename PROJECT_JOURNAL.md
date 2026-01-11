## [2026-01-10] Strict runtime/pytest DB URL resolution

### Fixed
- **Root issue**: app in development mode sometimes incorrectly resolved to `smartsell_test` when TEST_* environment variables were present, breaking production-safety invariant.
- **Solution**: Strict separation of DB URL resolution paths in `app/core/config.py`:
  - **Test mode** (PYTEST_CURRENT_TEST or TESTING=true or ENVIRONMENT in {test,testing}): prioritize TEST_ASYNC_DATABASE_URL > TEST_DATABASE_URL > assemble from TEST_DB_* parts; ignore DATABASE_URL/DB_*.
  - **Runtime** (otherwise): use **only** DATABASE_URL/DB_URL or assemble from DB_* parts; **ignore all TEST_*** variables entirely.
- URL assembly from parts: added fallback assembly in `resolve_database_url` and `resolve_async_database_url` to support TEST_DB_{USER,PASSWORD,HOST,PORT,NAME} (test mode) and DB_{USER,PASSWORD,HOST,PORT,NAME} (runtime).
- Enhanced logging: `db_url_resolved` log now includes contextual source (`source=(TEST_ASYNC_DATABASE_URL|TEST_DATABASE_URL|DATABASE_URL|DB_*|TEST_DB_*|...) (runtime|test)->async`) passed through from resolver to `app/core/db.py::_log_effective_url`.
- Updated tests:
  - `tests/test_db_runtime_vs_test_selection.py`: new test verifies runtime prefers DATABASE_URL over TEST_* and pytest mode prefers TEST_ASYNC_DATABASE_URL.
  - `tests/test_db_async_url_resolution.py`: updated existing tests to set PYTEST_CURRENT_TEST so strict resolver enables test mode.
  - `tests/test_db_url_priority.py`: adjusted `test_database_url_used_when_not_testing` to clear PYTEST_CURRENT_TEST and mock `_under_pytest()` for runtime path.

### Verified
- ruff format app/core/{config,db}.py tests/test_db_{async_url_resolution,runtime_vs_test_selection,url_priority}.py
- ruff check (passes)
- pytest -q (250 passed, 6 skipped)

## [2026-01-06] Kaspi sync state metrics

### Added
- Persisted Kaspi sync state metrics: last_attempt_at, last_duration_ms, last_result, last_fetched/inserted/updated with success/failure/locked outcomes and safe error recording.
- `/api/v1/kaspi/orders/sync/state` returns persisted metrics and error info; schemas updated accordingly.
- Coverage for defaults, success, failure, and locked runs with state assertions.

### Verified
- python -m ruff format app tests
- python -m ruff check app tests
- pytest -q *(fails: missing wallet_accounts/wallet_ledger/wallet_payments tables after alembic upgrade in test DB)*

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

## [2025-09-11] Repo bootstrap

### Added
- Repo created from uploaded archive (files 10.zip), immediately unpacked then replaced with a clean FastAPI/Alembic project skeleton (billing, wallet, payments, product models; routes; alembic baseline 20230910_161100_init; tests) per commits 0d718e6 → d575070.
- Dockerfile, docker-compose, CI workflow, and pytest scaffolding seeded from the cleaned structure.

### Notes
- Earlier SmartSell work lived in other repositories and was migrated here before this initial upload; this repo’s history starts from the imported archive.

## [2025-12-28] DB/CI cleanup and artifact hygiene

### Changed
- Pinned security scan workflow (trivy-action 0.33.1) and ignored bulky local scan artifacts to keep pipelines stable (84bd528, 7c81655).
- Widened alembic_version length and hardened offline migrations/tests; improved ICU reset defaults (kk-KZ-x-icu) and documented UTF-8 workflow with utf8_probe script (a1b9c36, b58fba1, 3211767).
- Removed generated DB_SETUP reports and repository artifacts to keep the tree clean (abc2b84, c5e30c4, 32212d0).

### Fixed
- Addressed pydantic/sqlalchemy deprecation warnings and stabilized test rollback behavior (5647f30).

### Notes
- Security/CI pinning and DB cleanup done ahead of end-of-year releases.

## [2025-12-25] Auth hardening and token fixes

### Fixed
- change_password now verifies current password and revokes sessions; token generation and OTP audit stabilized; user name properties corrected (ec1d322, ec34c2a, cbf38d4).

### Changed
- Ignored local test output artifacts to keep the tree clean (75e3e30).

### Notes
- Auth/e2e/campaign tests were updated to run via async_client with dependency overrides earlier in the week (5b655f2).

## [2025-12-24] Auth router + Alembic-first schema

### Added
- Mounted real `/api/auth` router and implemented `/api/auth/me`; bootstrap_schema.py marked DEV-ONLY and temp files ignored (a032613, 451e843, 5fc6770).

### Changed
- Disabled runtime create_all; enforced Alembic-managed schema and switched tests to `alembic upgrade head` (973f803, 78b70a3).
- Baseline migration corrected (deferred FKs, JSONB) and audit logger/bootstrap schema fixes (0c2ee83, b2793f0).

### Notes
- Campaign/auth/test wiring moved to dependency overrides for async sessions (5b655f2).

## [2025-12-23] API cleanup before auth/migration repair

### Changed
- Unified auth routing and removed legacy routers/duplicate models; aligned DB bootstrap and Kaspi/subscription models (7efbaac).
- WIP migration order and async/sync session fixes started (c54aebd).

### Notes
- Auth/e2e/campaign tests adjusted to async_client with dependency overrides to unblock CI (5b655f2).

## [2025-12-13] Repo re-import for dev/main alignment

### Added
- Re-imported full FastAPI stack (API v1, services, Alembic backups, frontend, tests, docs) with CI/CD/security workflows and alembic backups under _alembic_backup (1ed689e).
- Preserved legacy migrations/quarantine scripts for reference while preparing dev/main alignment.

### Notes
- Snapshot kept on backup/main-before-dev-2025-12-26.

## [2025-10-13] Repository hygiene and ignore normalization

### Changed
- Dropped committed venv/local DB artifacts; hardened .gitignore and .gitattributes; merged feat/all-in-one and backup/pre-sync snapshots (7842477, 9c9f7db, faca159, 603fc8c).
- Created sync-20251013-0105 tag/backups to preserve state before merging ignore changes (bbe70c5).

### Notes
- Upstream ignore files from origin/main were preserved for comparison.

## [2025-10-12] Ignore and attributes cleanup

### Changed
- Cleaned and reorganized .gitignore entries (a720bbf) and clarified .gitattributes (f8a13ae) to reduce churn ahead of snapshotting.

## [2025-10-03] Git attributes and CI hygiene

### Changed
- Refined .gitattributes for consistent text handling and merged feat/all-in-one via PR #1 (eec8b9d, df68d99, 724c3f1).
- Split CI/CD/security workflows and marked skip-CI/WIP commits while cleanup was in progress (11103bd, 5cb839e, 09f763b, 2aecba5).

## [2025-10-02] Initial SmartSell sync import

### Added
- Imported full FastAPI application with auth, campaigns, wallet/payments, OTP (Mobizon), Kaspi service, services/workers, Alembic migrations, and frontend scaffold (f24b8f9).
- Introduced CI/CD/security workflows, env templates, Makefile, requirements, and compliance/licensing docs; added database fixtures and tools.

### Changed
- Split CI/CD/security workflows and hardened .gitignore (local db/venv dropped) (11103bd, 7842477).

## [2025-09-16] Python 3.11 target

### Changed
- Set toolchain to Python 3.11 in setup.cfg, pyproject, and .python-version (7791c66, 3c0b339, 95bede3).

## [2025-09-17] Python 3.11 baseline and repo cleanup

### Changed
- Standardized Python target to 3.11 across setup.cfg, pyproject, and .python-version (7791c66, 95bede3).
- Removed .github bot directory, app/core, and bot usage docs to restart from a cleaned stack (2aa2c48, 5440097, b1a9989, 530387c, 5a3c89f).

## [2025-09-13] Bot automation and specs

### Added
- SmartSell Bot automation workflows with status/permissions checks and conflict-resolution commands; bot automation documentation (a27ef98, 6dc7248, 5ceb026, f375647).
- Added SmartSell Bot system instructions and platform specs (ТЗ на Flask / ТЗ на FastAPI) and GitHub Actions bot automation (1065091).

### Removed
- Legacy backend files cleared to restart clean (0ee7668).

### Notes
- Multiple Copilot fix PRs merged to stabilize bot workflow.

## [2025-09-12] FastAPI app + CI scaffolding

### Added
- Defined FastAPI app in app/main.py and aligned conftest imports; requirements updated for FastAPI dependencies; Swagger setup refactored (db3a10a, 70edd76, 4cfdf03, fa175a8).
- Added GitHub Actions auto-merge workflow and CI config to gate PRs (affde9b, 432c9be, 325d691).

### Notes
- Early CI adjustments kept dependency overrides working for tests (d6ecc04).

## [2026-01-02] Tenant scoping expansion + CI stability

### Added
- Tenant-scoped wallet/payments/subscriptions APIs and isolation tests landed (ca7e08b, b7db8e0, fdbeb42).

### Changed
- Standardized DB names for main/test and removed probe DB references; lazy-init wallet/payments storage and fail-fast imports to keep CI stable (1caeea1, 807f268, f1f3c24).
- Added safe_inspect fallback for offline Alembic and stabilized billing tenant tests; formatted wallet/payments tenant tests (32b6e1b, fedb8c7).

### Notes
- Local audit/report artifacts were removed from version control to keep the tree clean (c5e30c4, 32212d0).

## [2026-01-07] Current project state snapshot

### Notes
- API v1 is async-only (AsyncSession deps) with tenant scoping centralized in `resolve_tenant_company_id`; platform-admin overrides removed across wallet/payments/billing/campaigns/analytics/kaspi.
- Integration Center supports provider config storage and resolver hot-switching for OTP/messaging/payments with admin endpoints and health checks.
- Kaspi orders sync MVP ships with advisory lock, incremental watermarking, idempotent items/status history, and persisted sync metrics/error fields exposed via `/api/v1/kaspi/orders/sync/state`.
- CI baseline relies on ruff format/check + pytest gates; Alembic smoke runs and v0.1.0/v0.1.1 releases are published; latest feature branch builds inherit the green baseline from 2026-01-06 checks.
- Migrations cover wallet/payments tenant scope, kaspi state metrics, and offline-safe patterns; DB URL resolution is deterministic for async engines.

### Verified
- HEAD 8b36cc5 (feat/kaspi-sync-state-metrics-v1); rerun full suite (`python -m ruff format --check app tests tools`, `python -m ruff check app tests tools`, `python -m pytest -q`) after further changes.

## [2026-01-06] Kaspi sync state metrics

### Added
- Persisted Kaspi sync state metrics: last_attempt_at, last_duration_ms, last_result, last_fetched/inserted/updated with success/failure/locked outcomes and safe error recording.
- `/api/v1/kaspi/orders/sync/state` returns persisted metrics and error info; schemas updated accordingly.
- Coverage for defaults, success, failure, and locked runs with state assertions.

### Verified
- python -m ruff format app tests
- python -m ruff check app tests
- pytest -q *(fails: missing wallet_accounts/wallet_ledger/wallet_payments tables after alembic upgrade in test DB)*

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


## [2025-09-11] Repo bootstrap

### Added
- Repo created from uploaded archive (files 10.zip), immediately unpacked then replaced with a clean FastAPI/Alembic project skeleton (billing, wallet, payments, product models; routes; alembic baseline 20230910_161100_init; tests) per commits 0d718e6 → d575070.
- Dockerfile, docker-compose, CI workflow, and pytest scaffolding seeded from the cleaned structure.

### Notes
- Earlier SmartSell work lived in other repositories and was migrated here before this initial upload; this repo’s history starts from the imported archive.

## [2025-12-28] DB/CI cleanup and artifact hygiene

### Changed
- Pinned security scan workflow (trivy-action 0.33.1) and ignored bulky local scan artifacts to keep pipelines stable (84bd528, 7c81655).
- Widened alembic_version length and hardened offline migrations/tests; improved ICU reset defaults (kk-KZ-x-icu) and documented UTF-8 workflow with utf8_probe script (a1b9c36, b58fba1, 3211767).
- Removed generated DB_SETUP reports and repository artifacts to keep the tree clean (abc2b84, c5e30c4, 32212d0).

### Fixed
- Addressed pydantic/sqlalchemy deprecation warnings and stabilized test rollback behavior (5647f30).

### Notes
- Security/CI pinning and DB cleanup done ahead of end-of-year releases.

## [2025-12-25] Auth hardening and token fixes

### Fixed
- change_password now verifies current password and revokes sessions; token generation and OTP audit stabilized; user name properties corrected (ec1d322, ec34c2a, cbf38d4).

### Changed
- Ignored local test output artifacts to keep the tree clean (75e3e30).

### Notes
- Auth/e2e/campaign tests were updated to run via async_client with dependency overrides earlier in the week (5b655f2).

## [2025-12-24] Auth router + Alembic-first schema

### Added
- Mounted real `/api/auth` router and implemented `/api/auth/me`; bootstrap_schema.py marked DEV-ONLY and temp files ignored (a032613, 451e843, 5fc6770).

### Changed
- Disabled runtime create_all; enforced Alembic-managed schema and switched tests to `alembic upgrade head` (973f803, 78b70a3).
- Baseline migration corrected (deferred FKs, JSONB) and audit logger/bootstrap schema fixes (0c2ee83, b2793f0).

### Notes
- Campaign/auth/test wiring moved to dependency overrides for async sessions (5b655f2).

## [2025-12-23] API cleanup before auth/migration repair

### Changed
- Unified auth routing and removed legacy routers/duplicate models; aligned DB bootstrap and Kaspi/subscription models (7efbaac).
- WIP migration order and async/sync session fixes started (c54aebd).

### Notes
- Auth/e2e/campaign tests adjusted to async_client with dependency overrides to unblock CI (5b655f2).

## [2025-12-13] Repo re-import for dev/main alignment

### Added
- Re-imported full FastAPI stack (API v1, services, Alembic backups, frontend, tests, docs) with CI/CD/security workflows and alembic backups under _alembic_backup (1ed689e).
- Preserved legacy migrations/quarantine scripts for reference while preparing dev/main alignment.

### Notes
- Snapshot kept on backup/main-before-dev-2025-12-26.

## [2025-10-13] Repository hygiene and ignore normalization

### Changed
- Dropped committed venv/local DB artifacts; hardened .gitignore and .gitattributes; merged feat/all-in-one and backup/pre-sync snapshots (7842477, 9c9f7db, faca159, 603fc8c).
- Created sync-20251013-0105 tag/backups to preserve state before merging ignore changes (bbe70c5).

### Notes
- Upstream ignore files from origin/main were preserved for comparison.

## [2025-10-12] Ignore and attributes cleanup

### Changed
- Cleaned and reorganized .gitignore entries (a720bbf) and clarified .gitattributes (f8a13ae) to reduce churn ahead of snapshotting.

## [2025-10-03] Git attributes and CI hygiene

### Changed
- Refined .gitattributes for consistent text handling and merged feat/all-in-one via PR #1 (eec8b9d, df68d99, 724c3f1).
- Split CI/CD/security workflows and marked skip-CI/WIP commits while cleanup was in progress (11103bd, 5cb839e, 09f763b, 2aecba5).

## [2025-10-02] Initial SmartSell sync import

### Added
- Imported full FastAPI application with auth, campaigns, wallet/payments, OTP (Mobizon), Kaspi service, services/workers, Alembic migrations, and frontend scaffold (f24b8f9).
- Introduced CI/CD/security workflows, env templates, Makefile, requirements, and compliance/licensing docs; added database fixtures and tools.

### Changed
- Split CI/CD/security workflows and hardened .gitignore (local db/venv dropped) (11103bd, 7842477).

## [2025-09-16] Python 3.11 target

### Changed
- Set toolchain to Python 3.11 in setup.cfg, pyproject, and .python-version (7791c66, 3c0b339, 95bede3).

## [2025-09-17] Python 3.11 baseline and repo cleanup

### Changed
- Standardized Python target to 3.11 across setup.cfg, pyproject, and .python-version (7791c66, 95bede3).
- Removed .github bot directory, app/core, and bot usage docs to restart from a cleaned stack (2aa2c48, 5440097, b1a9989, 530387c, 5a3c89f).

## [2025-09-13] Bot automation and specs

### Added
- SmartSell Bot automation workflows with status/permissions checks and conflict-resolution commands; bot automation documentation (a27ef98, 6dc7248, 5ceb026, f375647).
- Added SmartSell Bot system instructions and platform specs (ТЗ на Flask / ТЗ на FastAPI) and GitHub Actions bot automation (1065091).

### Removed
- Legacy backend files cleared to restart clean (0ee7668).

### Notes
- Multiple Copilot fix PRs merged to stabilize bot workflow.

## [2025-09-12] FastAPI app + CI scaffolding

### Added
- Defined FastAPI app in app/main.py and aligned conftest imports; requirements updated for FastAPI dependencies; Swagger setup refactored (db3a10a, 70edd76, 4cfdf03, fa175a8).
- Added GitHub Actions auto-merge workflow and CI config to gate PRs (affde9b, 432c9be, 325d691).

### Notes
- Early CI adjustments kept dependency overrides working for tests (d6ecc04).

## [2026-01-02] Tenant scoping expansion + CI stability

### Added
- Tenant-scoped wallet/payments/subscriptions APIs and isolation tests landed (ca7e08b, b7db8e0, fdbeb42).

### Changed
- Standardized DB names for main/test and removed probe DB references; lazy-init wallet/payments storage and fail-fast imports to keep CI stable (1caeea1, 807f268, f1f3c24).
- Added safe_inspect fallback for offline Alembic and stabilized billing tenant tests; formatted wallet/payments tenant tests (32b6e1b, fedb8c7).

### Notes
- Local audit/report artifacts were removed from version control to keep the tree clean (c5e30c4, 32212d0).

## [2026-01-07] Current project state snapshot

### Notes
- API v1 is async-only (AsyncSession deps) with tenant scoping centralized in `resolve_tenant_company_id`; platform-admin overrides removed across wallet/payments/billing/campaigns/analytics/kaspi.
- Integration Center supports provider config storage and resolver hot-switching for OTP/messaging/payments with admin endpoints and health checks.
- Kaspi orders sync MVP ships with advisory lock, incremental watermarking, idempotent items/status history, and persisted sync metrics/error fields exposed via `/api/v1/kaspi/orders/sync/state`.
- CI baseline relies on ruff format/check + pytest gates; Alembic smoke runs and v0.1.0/v0.1.1 releases are published; latest feature branch builds inherit the green baseline from 2026-01-06 checks.
- Migrations cover wallet/payments tenant scope, kaspi state metrics, and offline-safe patterns; DB URL resolution is deterministic for async engines.

### Verified
- HEAD 8b36cc5 (feat/kaspi-sync-state-metrics-v1); rerun full suite (`python -m ruff format --check app tests tools`, `python -m ruff check app tests tools`, `python -m pytest -q`) after further changes.

## [2026-01-06] Kaspi sync state metrics (Recovered from c60547e^)


### Added
- Persisted Kaspi sync state metrics: last_attempt_at, last_duration_ms, last_result, last_fetched/inserted/updated with success/failure/locked outcomes and safe error recording.
- `/api/v1/kaspi/orders/sync/state` returns persisted metrics and error info; schemas updated accordingly.
- Coverage for defaults, success, failure, and locked runs with state assertions.

### Verified
- python -m ruff format app tests
- python -m ruff check app tests
- pytest -q *(fails: missing wallet_accounts/wallet_ledger/wallet_payments tables after alembic upgrade in test DB)*
## [2026-01-08] Docs / Repo hygiene
- merged notes from docs/PROJECT_JOURNAL.md into root journal (single source of truth; append-only)

---

## 2026-01-08: Clarification on Historical Database Names

During code audit, found references to old test database names (smartsell_test2, smartselltest2, smartsell_migrate_clean) in docs/DB_AUDIT_20251228_141740.md. These are **historical artifacts** from previous test runs and debugging sessions captured in audit logs.

**Current standard**: All tests use smartsell_test database name (defined via TEST_DATABASE_URL environment variable). The old names are not used in any active code, configuration, or scripts—they exist only as output snapshots in historical audit documents.

No action required on old references in docs—kept for historical record. All active code correctly uses smartsell_test.

## [2026-01-09] Kaspi orders sync MVP coverage

### Added
- Added MVP test suite for Kaspi orders sync: idempotency, watermark advancement/filtering, upsert updates, advisory lock (423), and error persistence.
- Added KASPI_SYNC_MVP_SUMMARY.md documenting verified behavior and test results.

### Verified
- python -m ruff format tests/app/api/test_kaspi_orders_sync_mvp.py
- python -m pytest tests/app/api/test_kaspi_orders_sync_mvp.py -q
- python -m pytest tests/ -q

## [2026-01-10] Kaspi Orders Sync Hardening (Locks + Timeout + Ops)

### Added
- **Transaction-scoped locks**: switched per-company advisory locks to `pg_try_advisory_xact_lock` so locks auto-release on commit/rollback.
- **Run timeout guard**: wrapped sync run with `asyncio.timeout` and record `timeout` errors without advancing watermarks.
- **Ops endpoint**: `GET /api/v1/kaspi/orders/sync/ops` returns sync state plus `lock_available` probe via short xact-lock attempt.

### Tests
- `test_sync_ops_lock_available_field` — ops endpoint includes lock_available field.
- `test_sync_timeout_records_error` — timeout returns 504 and persists error state without advancing watermark.

### Verified
- `python -m pytest -q` (248 passed, 6 skipped)
- `python -m ruff format app tests` + `python -m ruff check app tests`
- Timeout behavior: validated with deterministic test suite

## [2026-01-10] Kaspi Auto-Sync: Operational Observability (Configuration + Scheduler Visibility)

### Added
- **Enhanced Status Endpoint**: `GET /api/v1/kaspi/autosync/status` now returns full operational state
  - **Configuration fields**: `interval_minutes`, `max_concurrency` (from settings)
  - **Scheduler visibility**: `job_registered` (bool), `scheduler_running` (bool | null)
  - **Backward compatible**: All existing `last_run_summary` fields preserved
  - **Safe**: Returns valid response even if scheduler unavailable

- **Updated Response Model**: `KaspiAutoSyncStatusOut` reorganized with sections:
  1. Configuration (enabled, interval_minutes, max_concurrency)
  2. Scheduler state (job_registered, scheduler_running)
  3. Last run summary (last_run_at, eligible_companies, success, locked, failed)

- **Tests Added**: Comprehensive coverage for new fields
  - `test_autosync_status_includes_config` - Verifies configuration fields returned
  - `test_autosync_status_includes_scheduler_state` - Verifies scheduler visibility when running
  - `test_autosync_status_job_not_registered` - Verifies job_registered reflects actual state

### Decision Rationale
- **Why configuration in status?**: Operators need quick visibility into active settings
- **Why scheduler state?**: Helps diagnose if background job is properly registered
- **Why safe defaults?**: Never fails even if scheduler/module unavailable

### Verified
- All 246 tests passing (10 autosync tests including 3 new observability tests, 1 skipped)
- Fixed test mocking: Using `patch.dict(sys.modules)` for dynamic imports inside endpoints
- Backward compatible (no breaking changes)
- Safe fallbacks for unavailable components

## [2026-01-10] Kaspi Auto-Sync: Production Safety (Disabled by Default)

### Changed
- **Configuration Default**: Changed `KASPI_AUTOSYNC_ENABLED` from `default=True` to `default=False` in `app/core/config.py`
  - **Rationale**: Production safety - auto-sync must be explicitly enabled
  - **Impact**: New deployments will not auto-sync until enabled via environment variable

- **API Endpoints Enhanced**: Updated `app/api/v1/kaspi.py`
  - `GET /api/v1/kaspi/autosync/status` now returns `enabled: bool` field
  - Returns `enabled=false` with zero stats when disabled
  - `POST /api/v1/kaspi/autosync/trigger` now returns 409 Conflict when disabled
  - Clear error message: "Kaspi auto-sync is disabled. Set KASPI_AUTOSYNC_ENABLED=true to enable."

### Added Tests
- `test_autosync_status_disabled` - Verifies status endpoint shows disabled state
- `test_autosync_trigger_disabled` - Verifies trigger returns 409 when disabled
- Updated existing tests to check for `enabled` field

### Updated Documentation
- `KASPI_AUTOSYNC_IMPLEMENTATION.md` - Updated configuration defaults section
- Added warning about production safety in deployment notes

### Decision Rationale
- **Why disabled by default?**: Prevents unexpected behavior in production environments
- **Why 409 Conflict?**: Semantic HTTP status for operational state conflict
- **Why explicit enable?**: Forces conscious decision to enable background jobs

### Verified
- All tests passing (243 passed, 6 skipped)
- ruff format/check clean
- API endpoints behave correctly in disabled state

## [2026-01-10] Kaspi Orders Auto-Sync Scheduler (Production-Grade)

### Added
- **Background Job**: Implemented periodic auto-sync scheduler in `app/worker/kaspi_autosync.py`
  - Uses existing APScheduler infrastructure (BackgroundScheduler)
  - Configurable interval via `KASPI_AUTOSYNC_INTERVAL_MINUTES` (default: 15 minutes)
  - Runs in background daemon thread with event listeners

- **Concurrency Control**: Batch processing with asyncio.gather
  - Max parallel syncs configurable via `KASPI_AUTOSYNC_MAX_CONCURRENCY` (default: 3)
  - Chunking strategy to avoid overwhelming the system on Windows
  - Safe for multi-company environments

- **Company Selection**: Eligible companies query
  - Filters: `is_active=True AND deleted_at IS NULL AND kaspi_store_id IS NOT NULL`
  - Implemented in `_get_eligible_companies(db)` with SQLAlchemy async

- **Error Handling**: Graceful degradation
  - Advisory lock respected: `KaspiSyncAlreadyRunning` → counted as "locked"
  - Generic errors → counted as "failed", don't stop other companies
  - Per-company error logging with safe formatting (no credentials)

- **Operational Endpoints**: Admin API in `app/api/v1/kaspi.py`
  - `GET /api/v1/kaspi/autosync/status` - Last run summary (eligible/success/locked/failed counts)
  - `POST /api/v1/kaspi/autosync/trigger` - Manual trigger for immediate sync

- **Configuration**: Three new settings in `app/core/config.py` (lines 431-450)
  - `KASPI_AUTOSYNC_ENABLED: bool` - Enable/disable auto-sync (default: False, changed for production safety)
  - `KASPI_AUTOSYNC_INTERVAL_MINUTES: int` - Sync frequency (default: 15)
  - `KASPI_AUTOSYNC_MAX_CONCURRENCY: int` - Parallel sync limit (default: 3)

- **Scheduler Integration**: Updated `app/worker/scheduler_worker.py`
  - Added job ID constant: `_JOB_ID_KASPI_AUTOSYNC`
  - Registered job in `start()` function with IntervalTrigger
  - Registered job in `reload_jobs()` for hot reload support
  - Conditional registration based on `KASPI_AUTOSYNC_ENABLED` setting

- **Tests**: Comprehensive test suite in `tests/test_kaspi_autosync.py`
  - `test_get_eligible_companies_filters_correctly` - Validates company filtering logic
  - `test_sync_respects_concurrency_limit` - Ensures max_concurrency honored
  - `test_locked_companies_dont_stop_batch` - Advisory lock doesn't break batch
  - `test_failed_companies_tracked_in_summary` - Errors tracked, don't crash job
  - `test_manual_trigger_via_endpoint` - API trigger works correctly
  - `test_autosync_status_endpoint` - Status endpoint returns valid data

### Technical Details
- **Pattern**: Follows existing `process_campaigns` job pattern
- **Trigger**: `IntervalTrigger(minutes=settings.KASPI_AUTOSYNC_INTERVAL_MINUTES)`
- **APScheduler Config**: `max_instances=1`, `coalesce=True`, `misfire_grace_time=300`
- **Global State**: `_last_run_summary` dict tracks last run statistics
- **Database**: Uses async SQLAlchemy sessions with proper cleanup
- **Logging**: Structured logging with company_id context, no sensitive data

### Decision Rationale
- **Why APScheduler?**: Already in use, battle-tested, supports multiple triggers
- **Why batch processing?**: Avoids thundering herd, controlled resource usage
- **Why global state?**: Simple operational visibility without DB overhead
- **Why admin endpoints?**: Operational debugging and manual intervention capability

### Verified
- All existing tests pass (236 passed, 5 skipped)
- New tests created for auto-sync functionality
- Safe logging verified (no tokens/credentials exposed)
- Scheduler integration tested with hot reload

## [2026-01-10] Kaspi Orders Sync — Timeout persistence + hardened tests

### Context
Timeout could cancel/rollback the main sync transaction, causing `kaspi_order_sync_state` updates to be lost and making `/state` observability unreliable under timeouts.

### Changes
- Added `_record_timeout_state` to persist timeout result using a fresh async engine session (fallback to the original session when needed).
- Ensured the timeout path records:
  - `last_result="failed"`
  - `last_error_code="timeout"`
  - `last_attempt_at` set
  - watermark preserved (no forward move on timeout)
- Strengthened `test_sync_timeout_records_error` to assert HTTP 504 and persisted sync_state invariants.

### Impact
- Timeout behavior is now operationally observable and durable.
- Test suite enforces the contract (no "504-only" weakening).

### Verification
- `python -m ruff format app tests`
- `python -m ruff check app tests`
- `python -m pytest -q` → `248 passed, 6 skipped`
### 2026-01-11 — Registration creates draft Company tenant

**Context**
- `/api/v1/auth/register` создавал пользователя, но tenant-компания не создавалась → таблица `companies` оставалась пустой, tenant-scoping и онбординг ломались.

**Changes**
- `app/api/v1/auth.py`: в `register()` добавлено создание **draft Company** в одной транзакции:
  - `company.name = user_data.company_name` или fallback `Draft {normalized_phone}`
  - `company.is_active = true`, `company.subscription_plan = 'start'`
  - связь: `company.owner_id = user.id`, `user.company_id = company.id`
- `tests/app/test_auth.py`: добавлены регрессионные тесты:
  - `test_register_creates_draft_company_tenant`
  - `test_register_creates_company_with_default_name`
  - тесты учитывают нормализацию телефона (хранится только digits, без '+').
- Добавлена документация: `docs/REGISTRATION_COMPANY_TENANT.md`
- Quality gate: `ruff format/check` пройдено; `pytest tests/app/test_auth.py` зелёный.

**Impact**
- После регистрации всегда существует tenant `Company`, а `User` привязан к ней.
- Появилась стабильная основа для онбординга (позже переименование/реквизиты на шаге подключения Kaspi).

**Follow-ups**
- В онбординге подключения Kaspi сделать `company_name` обязательным и обновлять `companies.name` (источник истины — ввод владельца, не маркетплейс).
- При желании добавить флаг onboarding в `companies.settings` (например `kaspi_connected`), и запускать autosync только при активной интеграции.

