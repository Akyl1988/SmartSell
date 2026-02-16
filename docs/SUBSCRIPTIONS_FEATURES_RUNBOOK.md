# Subscriptions, Plans, and Features Runbook

## Overview
This runbook explains how to manage plans, features, and limits without deployment.

## Admin Endpoints (platform_admin)
- `POST /api/v1/admin/plans` create a plan (code is immutable)
- `PATCH /api/v1/admin/plans/{code}` update name/price/is_active/trial_days_default
- `POST /api/v1/admin/features` create a feature (code is immutable)
- `PATCH /api/v1/admin/features/{code}` update name/description/is_active
- `PUT /api/v1/admin/plan-features/{plan_code}/{feature_code}` enable/disable and set limits JSON
- `POST /api/v1/admin/subscriptions/activate` activate a paid plan for a company
- `POST /api/v1/admin/subscriptions/trial/kaspi` grant a Kaspi trial plan

## Enable a Feature on a Plan
1) Ensure plan and feature exist.
2) Enable it with limits:

```json
PUT /api/v1/admin/plan-features/pro/repricing
{
  "enabled": true,
  "limits": {
    "max_products_per_period": 100
  }
}
```

## Activate a Plan
```json
POST /api/v1/admin/subscriptions/activate
{
  "companyId": 1001,
  "plan": "pro"
}
```

## Grant a Trial
```json
POST /api/v1/admin/subscriptions/trial/kaspi
{
  "companyId": 1001,
  "merchant_uid": "KASPI-123",
  "plan": "pro",
  "trial_days": 15
}
```

## Limits
- Repricing uses `max_products_per_period`.
- Preorders uses `max_preorders_per_period`.
