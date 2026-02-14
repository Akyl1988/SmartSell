# Campaign Queue Ops

This runbook covers operational checks for the campaign processing queue.

## Tunables

- `CAMPAIGN_PROCESS_BATCH` (default 50): max campaigns processed per worker tick.
- `CAMPAIGN_MAX_ATTEMPTS` (default 3): max processing retries; set to 0 to disable the guard.

## Recognizing max attempts exceeded

A campaign can be marked as failed with:
- `processing_status=failed`
- `last_error=max_attempts_exceeded`

This means automatic retries stopped. To recover:
1) Use the admin requeue endpoint or the admin run endpoint.
2) Confirm attempts reset and status is `queued`.

## Scheduler lock busy

The scheduler uses an advisory lock to prevent overlapping ticks. If logs show
"scheduler lock busy", it usually means another scheduler tick is in progress.
No action is required unless it stays busy for an extended period.

## Force requeue caution

`force=true` on requeue can produce duplicate work if the campaign is actively
processing. Avoid using force requeue while a campaign is still executing.
Only use it to recover a stuck or orphaned campaign after you confirm no active
worker is processing the campaign.

## Smoke script

Use the smoke script for quick checks:

```
pwsh -NoProfile -File .\scripts\smoke-admin-campaign-queue.ps1 -BaseUrl http://127.0.0.1:8000 -Email admin@local -Password admin
```
