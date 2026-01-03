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
