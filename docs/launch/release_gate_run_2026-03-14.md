# RELEASE_GATE_RUN_2026-03-14

- Date/Time: 2026-03-14
- Candidate branch: dev
- Candidate commit: 52c498b

## Planned checks

1. ruff check .
2. ruff format .
3. pytest -q tests/app/api/test_openapi_legacy_contract.py
4. pytest -q tests/test_health_and_ready.py
5. pytest -q tests/test_platform_admin_tenant_access_policy.py tests/app/test_tenant_isolation_billing.py tests/app/test_tenant_isolation_subscriptions.py tests/app/api/test_subscriptions_api.py tests/app/test_wallet_invariants.py tests/app/api/test_kaspi_rbac.py tests/app/api/test_preorders_rbac_tenant.py

## Results

### git status -sb

~~~text
## dev...origin/dev
?? docs/launch/
~~~

### ruff check .

~~~text
PASS
~~~

### ruff format .

~~~text
505 files left unchanged
~~~

### pytest -q tests/app/api/test_openapi_legacy_contract.py

~~~text
2 passed in 14.79s
~~~

### pytest -q tests/test_health_and_ready.py

~~~text
5 passed in 7.67s
~~~

### pytest -q tests/test_platform_admin_tenant_access_policy.py tests/app/test_tenant_isolation_billing.py tests/app/test_tenant_isolation_subscriptions.py tests/app/api/test_subscriptions_api.py tests/app/test_wallet_invariants.py tests/app/api/test_kaspi_rbac.py tests/app/api/test_preorders_rbac_tenant.py

~~~text
45 passed in 61.26s (0:01:01)
~~~

## Verdict

- Release gate status: PASS
- Notes:
  - Candidate passed launch-critical baseline checks for lint, OpenAPI contract, readiness, tenant isolation, billing/subscriptions, wallet invariants, Kaspi RBAC, and preorder tenant safety.
  - Current untracked changes are launch evidence documents under docs/launch/, not code changes.
- Blocking issues:
  - None from this Day 2 release-gate run itself.
  - Remaining launch blockers are operational/governance blockers from the scorecard, not code/test blockers from this evidence run.
