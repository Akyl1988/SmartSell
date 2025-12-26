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
