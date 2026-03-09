# SmartSell API Lifecycle Policy (MVP)

Version: 2026-03-09
Status: Policy + minimal metadata support

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
