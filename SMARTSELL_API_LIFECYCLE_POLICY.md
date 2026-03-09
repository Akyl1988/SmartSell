# SmartSell API Lifecycle Policy (MVP)

Version: 2026-03-09
Status: Policy + endpoint-level lifecycle metadata registry

## 1) Versioning model

- Current supported version is `/api/v1`.
- New major API versions are introduced as `/api/v2`, `/api/v3`, etc.
- `v1` remains supported until an explicit sunset decision and communication.

## 2) Compatibility rules

- Additive changes are allowed within the same major version (new optional fields, new endpoints).
- Breaking changes require a new major API version.

## 3) Deprecation process

- An endpoint may be marked as deprecated before removal.
- Deprecated responses may include header: `Deprecation: true`.

## 4) Sunset process

- APIs approaching end-of-support may include header: `Sunset: <date>`.
- Sunset date must be communicated to clients in advance.

## 5) Operator responsibility

- Platform admin is responsible for notifying clients about deprecation/sunset windows.

## 6) Implemented lifecycle registry evidence (2026-03-09)

- Lifecycle headers are emitted centrally via `app/core/api_lifecycle.py` and applied in middleware (`app/main.py`).
- A minimal endpoint-level registry is implemented for deprecated compatibility routes using method+path matching.
- Current registered deprecated endpoint rollout:
	- `POST /api/v1/kaspi/feed/uploads/{upload_id}/refresh-status`
	- Headers: `Deprecation: true`, `Sunset: Tue, 30 Jun 2026 00:00:00 GMT`, `X-SmartSell-API-Version: v1`
- Test evidence:
	- `pytest tests/app/api/test_api_lifecycle_headers.py -q` -> `2 passed`
