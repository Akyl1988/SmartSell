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
- Notes: existing warnings remain (Pydantic v1 @validator deprecations, SQLAlchemy Query.get legacy, Trio deprecations).

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
