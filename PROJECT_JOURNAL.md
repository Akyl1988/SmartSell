## [2025-12-26] Admin Integrations: listing & events API
- Added: provider listing endpoint with filters + pagination (service layer + admin API).
- Added: events listing endpoint with filters (domain/provider/actor) + pagination; ordered results.
- Tests: extended tests/test_admin_integrations.py for listing + events filtering; pytest green (warnings only).
- Notes: existing warnings remain (Pydantic v1 @validator deprecations, SQLAlchemy Query.get legacy, Trio deprecations).

## [2025-12-26] OTP / Integrations
- added: runtime OTP provider resolution (OtpProviderResolver) with caching and safe fallback when registry/redis unavailable
- changed: OTP endpoints use resolver via DI (get_otp_service); hot-switch supported without restart
- tests: added test_otp_provider_hot_switch; alembic upgrade head OK; pytest -q OK (109 passed, 5 skipped)
