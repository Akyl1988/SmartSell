# SMARTSELL_RUNTIME_OWNERSHIP

## 1. Purpose
Define explicit runtime ownership between API and background runtimes so SmartSell can operate predictably for first-client launch without hero-mode ambiguity.

## 2. Current risk
- Ownership can become mixed when API, worker, and scheduler behaviors are not explicitly separated.
- Deployment/startup ambiguity can lead to duplicate background execution, missed jobs, or incident response confusion.
- For current maturity, this item remains Partial until verified repeatedly in real operations.

## 3. Runtime roles
- API runtime: request-serving process only.
- Worker runtime: executes asynchronous/background jobs.
- Scheduler runtime: decides when scheduled jobs should be enqueued or triggered.

## 4. API responsibilities
- Accept and process HTTP API requests.
- Perform synchronous validation, authorization, and request-bound business logic.
- Return deterministic responses and request identifiers for support/debugging.
- Publish background work requests/events (where applicable), but not execute long-running jobs inline.

## 5. Worker / scheduler responsibilities
- Worker owns long-running or retryable background execution.
- Scheduler owns timed/periodic dispatch decisions and handoff to worker.
- Background runtimes own retry policy, backoff, and operational observability for async tasks.
- Worker/scheduler must run as explicit process roles, not implicit API side-effects.

## 6. What must never run inside API request-serving mode
- Continuous polling loops.
- Periodic scheduler loops.
- Long-running batch jobs that exceed request lifecycle intent.
- Multi-tenant bulk reconciliation/sync sweeps as inline request work.
- Any process role that can run independently of a single request/response cycle.

## 7. Startup / deployment expectations
- API deployment starts API role only.
- Worker deployment starts worker role only.
- Scheduler deployment starts scheduler role only (or an explicitly documented combined control-plane role).
- Startup commands and run modes must be explicit in operations/deploy scripts.
- Any temporary mixed-mode startup must be documented as an exception with owner and expiry.

## 8. Evidence required to move from Partial to Exists
- Documented runtime startup commands for API, worker, and scheduler are present and used.
- At least one release/deploy record shows separated process ownership in practice.
- No production incident caused by API/background role ambiguity over a defined observation window.
- Operator runbook includes clear ownership triage: "API issue" vs "worker/scheduler issue".
