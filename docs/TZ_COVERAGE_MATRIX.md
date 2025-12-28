# ТЗ Coverage Matrix (FastAPI)

| Requirement | Where implemented | Status | Risks / Comments |
| --- | --- | --- | --- |
| Auth (phone/JWT/refresh) | `app/api/auth.py`, `app/core/security.py`, user models | Partial | SECRET_KEY default weak; OTP via Mobizon present; refresh/denylist exists; rate limiting optional. |
| OTP SMS (Mobizon) | `app/integrations/mobizon.py`, `app/integrations/sms_base.py` | Partial | Basic send; no idempotency/rate limit; no delivery tracking. |
| Products CRUD | `app/api/v1/products.py`, product models | Partial | CRUD present; import/export, Cloudinary upload not evident. |
| Orders sync (Kaspi) | `app/integrations/kaspi_adapter.py` | Partial | Adapter scaffold; end-to-end sync/status handling not covered by tests. |
| Warehouses/stock | Models under `app/models/warehouse*` (check), routes? | Partial | Coverage unclear; no dedicated API tests. |
| Billing & subscriptions | `app/api/v1/subscriptions.py`, `app/models/billing.py` | Done (MVP) | Async endpoints tested; payments provider integration TBD. |
| Payments TipTop Pay | Not found | Todo | No client/webhook/endpoints; keys storage not implemented. |
| Messaging / Campaigns | `app/services`/`integrations` stubs, tests for campaigns | Partial | Needs provider implementations, rate limits, templates. |
| Analytics/Reports | Not found | Todo | No charts/exports endpoints. |
| AI Bot-assistant | Not found | Todo | No runtime/UI for AI bot described in ТЗ. |
| Frontend React/MUI | `frontend/` scaffold | Partial | Lacks feature parity with backend modules. |
| Security/Observability | Middleware in `app/main.py`, slowapi optional | Partial | Debug DB endpoint exposed; TrustedHost/HTTPSRedirect optional; metrics optional. |
| Migrations/DB safety | `migrations/` with single head | Partial | .bak/quarantine artifacts; ALLOW_DROPS flag. |
