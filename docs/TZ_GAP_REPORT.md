
# TZ Gap Report (FastAPI)

Legend: ✅ implemented, ⚠️ partially implemented, ❌ no evidence found

## Scope and Method
- Source of truth: TZ na FastAPI (plus SmartSell v16 addendum)
- Evidence sources: FastAPI routers, services, models, and workers in this repo
- Strict evidence only: items marked ✅/⚠️ include file-level proof links; ❌ indicates no proof found in code search

## Summary
- Core auth, user management, products, billing/subscriptions, analytics, reports/exports, and Kaspi integration have concrete implementations.
- Order management, warehouse management APIs, AI bot, and WhatsApp/Telegram messaging are not evidenced by code.
- Several advanced requirements (dumping, preorders, audits) are partially supported at model/service level without full API proof.

## Feature Checklist
| Section | Requirement | Status | Evidence |
| --- | --- | --- | --- |
| Auth | Register/login/refresh/logout | ✅ | [app/api/v1/auth.py](app/api/v1/auth.py#L395-L735) |
| Auth | OTP request/verify | ✅ | [app/api/v1/auth.py](app/api/v1/auth.py#L1448-L1494) |
| Auth | SMS provider (Mobizon) | ✅ | [app/services/mobizon_service.py](app/services/mobizon_service.py#L1-L120) |
| Users/Roles | Company user list + activate/deactivate + role change | ✅ | [app/api/v1/users.py](app/api/v1/users.py#L26-L212) |
| Products | Product CRUD (list/create/update/delete) | ✅ | [app/api/v1/products.py](app/api/v1/products.py#L219-L348) |
| Products | Stock read/update | ✅ | [app/api/v1/products.py](app/api/v1/products.py#L375-L430) |
| Products | Categories list/get | ✅ | [app/api/v1/products.py](app/api/v1/products.py#L737-L788) |
| Products | Repricing/dumping logic | ⚠️ | [app/services/repricing_service.py](app/services/repricing_service.py#L1-L220) |
| Products | Preorder/dumping fields | ⚠️ | [app/models/product.py](app/models/product.py#L360-L408) |
| Orders | Order domain model | ⚠️ | [app/models/order.py](app/models/order.py#L1-L200) |
| Orders | Order CRUD/management API | ❌ | No order router found under [app/api/v1/](app/api/v1/) (see Notes) |
| Invoices | Invoice CRUD | ✅ | [app/api/v1/invoices.py](app/api/v1/invoices.py#L37-L210) |
| Payments | TipTop webhook | ✅ | [app/api/v1/payments.py](app/api/v1/payments.py#L298-L356) |
| Payments | Refund/cancel | ✅ | [app/api/v1/payments.py](app/api/v1/payments.py#L377-L444) |
| Payments | Payment intents | ✅ | [app/api/v1/payments.py](app/api/v1/payments.py#L504-L566) |
| Payments | TipTop integration service | ✅ | [app/services/tiptop_service.py](app/services/tiptop_service.py#L1-L200) |
| Wallet | Wallet module (health + storage-backed API scaffold) | ⚠️ | [app/api/v1/wallet.py](app/api/v1/wallet.py#L282-L316) |
| Subscriptions | Plans + CRUD + cancel/renew | ✅ | [app/api/v1/subscriptions.py](app/api/v1/subscriptions.py#L251-L518) |
| Analytics | Dashboard + sales/customers/products | ✅ | [app/api/v1/analytics.py](app/api/v1/analytics.py#L130-L420) |
| Reports | Orders CSV | ✅ | [app/api/v1/reports.py](app/api/v1/reports.py#L459-L520) |
| Reports | Sales PDF | ✅ | [app/api/v1/reports.py](app/api/v1/reports.py#L799-L830) |
| Exports | Orders/Sales/Products XLSX | ✅ | [app/api/v1/exports.py](app/api/v1/exports.py#L202-L276) |
| Campaigns | Campaigns API scaffold + run endpoint | ⚠️ | [app/api/v1/campaigns.py](app/api/v1/campaigns.py#L765-L872) |
| Campaigns | Background processing worker | ✅ | [app/worker/campaign_processing.py](app/worker/campaign_processing.py#L1-L200) |
| Platform | platform_admin task endpoints | ✅ | [app/api/v1/admin.py](app/api/v1/admin.py#L30-L230) |
| Audit | Audit log model | ⚠️ | [app/models/audit_log.py](app/models/audit_log.py#L1-L120) |
| Warehouse/Inventory | Warehouse + stock movement models | ⚠️ | [app/models/warehouse.py](app/models/warehouse.py#L1-L200) |
| Integrations | Kaspi router endpoints | ✅ | [app/api/v1/kaspi.py](app/api/v1/kaspi.py#L1-L121) |
| Integrations | Kaspi auto-sync worker | ✅ | [app/worker/kaspi_autosync.py](app/worker/kaspi_autosync.py#L1-L200) |
| Integrations | Integration events API | ✅ | [app/api/v1/integrations.py](app/api/v1/integrations.py#L1-L80) |
| Integrations | Cloudinary image service | ✅ | [app/services/cloudinary_service.py](app/services/cloudinary_service.py#L1-L120) |
| AI Bot | AI chatbot/assistant | ❌ | No evidence found in [app/](app/) or [app/services/](app/services/) |
| Messaging | WhatsApp/Telegram messaging | ❌ | No evidence found in [app/](app/) or [app/services/](app/services/) |

## Targeted Gap Updates

### Auth / Logout revoke
- [x] /auth/logout makes access tokens invalid immediately.
- Implementation:
	- denylist revoke (jti + token hash) plus in-memory access-token revoke fallback.
	- get_current_user and get_current_user_optional always check revoke.
- Coverage:
	- [tests/app/test_auth.py](tests/app/test_auth.py#L576-L612)

### Preorders + Inventory
- [x] Preorder confirm/cancel/fulfill uses warehouse reservation flow.
- Implementation:
	- reservation helpers on ProductStock + StockMovement.
	- warehouse selection: main -> first active.
	- no Product.stock_quantity fallback for reservations.
- Coverage:
	- [tests/services/test_inventory_reservation_service.py](tests/services/test_inventory_reservation_service.py#L1-L210)
	- [tests/app/api/test_preorders_inventory.py](tests/app/api/test_preorders_inventory.py#L1-L700)

### Campaigns storage (in-memory vs ORM)
- [x] Prevent silent divergence between in-memory and ORM storage.
- Implementation:
	- centralized guard for storage-only endpoints in ORM mode.
	- 409 with detail=campaigns_orm_mode_not_supported_for_this_endpoint.
- Status:
	- in-memory: full functionality.
	- ORM: limited mode (guarded endpoints are blocked but behavior is explicit).
- Smoke:
	- [scripts/smoke-campaigns-e2e.ps1](scripts/smoke-campaigns-e2e.ps1#L214-L238)

## Notes and Gaps
- Orders API: no router file found for order CRUD or status management under [app/api/v1/](app/api/v1/) (search for *order*.py returned none).
- Warehouses/Inventory: strong model layer exists but no API endpoints evidenced.
- Repricing/dumping and preorder capabilities exist in models/services, but API endpoints and workflows are not fully verified.
- Reports cover CSV/PDF/XLSX for some datasets; additional report types from the TZ are not evidenced here.
- AI bot and messenger integrations (WhatsApp/Telegram) are not present in the codebase.
