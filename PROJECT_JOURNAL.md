## [2025-12-26] Admin Integrations: listing & events API
- Added: provider listing endpoint with filters + pagination (service layer + admin API).
- Added: events listing endpoint with filters (domain/provider/actor) + pagination; ordered results.
- Tests: extended tests/test_admin_integrations.py for listing + events filtering; pytest green (warnings only).
- Notes: existing warnings remain (Pydantic v1 @validator deprecations, SQLAlchemy Query.get legacy, Trio deprecations).
