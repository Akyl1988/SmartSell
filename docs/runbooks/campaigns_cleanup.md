# Campaigns Cleanup Runbook

This runbook describes how to clean up old campaign queue records.

## When to run

- The admin campaign queue is large and full of old DONE/FAILED entries.
- You want to keep the queue manageable without touching active items.

## Endpoint

`POST /api/v1/admin/tasks/campaigns/cleanup/run?done_days=14&failed_days=30&limit=500`

Parameters:
- `done_days` (1..365): cleanup DONE campaigns older than this many days.
- `failed_days` (1..365): cleanup FAILED campaigns older than this many days.
- `limit` (required): max campaigns to process in a single run.

Response includes:
- `deleted_campaigns`, `deleted_messages`
- `scanned_done`, `scanned_failed`
- `request_id`

## Examples

curl:
```
curl -X POST "http://127.0.0.1:8000/api/v1/admin/tasks/campaigns/cleanup/run?done_days=14&failed_days=30&limit=500" \
  -H "Authorization: Bearer <access_token>"
```

PowerShell:
```
$headers = @{ Authorization = "Bearer <access_token>" }
Invoke-RestMethod -Method POST -Uri "http://127.0.0.1:8000/api/v1/admin/tasks/campaigns/cleanup/run?done_days=14&failed_days=30&limit=500" -Headers $headers
```

## Safety notes

- Only DONE/FAILED campaigns older than the cutoff are cleaned up.
- Queued/processing/retrying campaigns are untouched.
- In production, this endpoint is allowed for platform admins and superusers.

## Automatic scheduler mode

The cleanup job runs automatically when the scheduler is enabled.

Required env:
- `ENABLE_SCHEDULER=1`
- `PROCESS_ROLE=web|worker|scheduler` (role must allow scheduler startup)

Schedule:
- Every 12 hours (interval job in APScheduler).

How to verify:
- Check logs for `Campaign cleanup job finished` with counters.
- Optionally run the manual endpoint and compare counters.
