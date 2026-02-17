# Campaigns E2E Smoke

This smoke test verifies the campaign pipeline end-to-end: enqueue, process, and queue visibility.

## Run

```
pwsh -NoProfile -File .\scripts\smoke-campaigns-e2e.ps1 -BaseUrl http://127.0.0.1:8000 -Email platform@local -Password admin -CompanyId 1
```

Optional flags:
- `-Limit 20` to limit queue output.
- `-AllowFailure` to exit 0 even if the campaign ends in failed state.

## Expected output

- Login success and masked token.
- Attempted seed (dev/test only) or a note that seeding is unavailable.
- `POST /api/v1/admin/tasks/campaigns/run` success.
- Queue list printed with key fields.
- Final status reported as `done` (preferred).

## If it fails

- If final status is `failed` with `last_error=max_attempts_exceeded`, the script will requeue once and re-run the task.
- If the seed endpoint is disabled (production), ensure there is at least one due campaign for the provided company.
- Use `-AllowFailure` if you only need smoke visibility without enforcing `done`.
