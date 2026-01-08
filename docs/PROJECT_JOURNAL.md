
---

## 2026-01-08: Clarification on Historical Database Names

During code audit, found references to old test database names (smartsell_test2, smartselltest2, smartsell_migrate_clean) in docs/DB_AUDIT_20251228_141740.md. These are **historical artifacts** from previous test runs and debugging sessions captured in audit logs.

**Current standard**: All tests use smartsell_test database name (defined via TEST_DATABASE_URL environment variable). The old names are not used in any active code, configuration, or scripts—they exist only as output snapshots in historical audit documents.

No action required on old references in docs—kept for historical record. All active code correctly uses smartsell_test.

