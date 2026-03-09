from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError, ProgrammingError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError
from app.models.billing import BillingPayment, Subscription
from app.models.campaign import Campaign
from app.models.company import Company
from app.models.order import Order
from app.models.preorder import Preorder
from app.models.product import Product
from app.models.repricing import RepricingRule
from app.models.user import User
from app.models.warehouse import Warehouse
from app.schemas.tenant_export import TenantExportManifestOut

EXPORT_SCOPE_VERSION = "tenant-export-mvp-v1"

INCLUDED_SECTIONS = [
    "company",
    "users",
    "products",
    "orders",
    "preorders",
    "campaigns",
    "subscriptions_billing_summary",
    "repricing_rules",
    "warehouses_inventory_summary",
]

NOT_INCLUDED = [
    "binary_media_bulk_dump",
    "external_provider_secrets_raw_values",
    "full_infra_backups",
    "hard_delete",
]


async def _safe_count(db: AsyncSession, stmt, *, section: str, warnings: list[str]) -> int:
    try:
        value = (await db.execute(stmt)).scalar_one()
        return int(value or 0)
    except (OperationalError, ProgrammingError):
        warnings.append(f"section_unavailable:{section}")
        return 0


async def build_tenant_export_manifest(
    db: AsyncSession,
    *,
    company_id: int,
    exported_by: str | None,
) -> TenantExportManifestOut:
    company = await db.get(Company, company_id)
    if not company:
        raise NotFoundError("company_not_found", code="company_not_found", http_status=404)

    warnings: list[str] = [
        "preview_only_manifest_no_file_generated",
        "secrets_and_tokens_are_not_exported",
    ]

    users_count = await _safe_count(
        db,
        select(func.count(User.id)).where(User.company_id == company_id, User.deleted_at.is_(None)),
        section="users",
        warnings=warnings,
    )
    products_count = await _safe_count(
        db,
        select(func.count(Product.id)).where(Product.company_id == company_id, Product.deleted_at.is_(None)),
        section="products",
        warnings=warnings,
    )
    orders_count = await _safe_count(
        db,
        select(func.count(Order.id)).where(Order.company_id == company_id),
        section="orders",
        warnings=warnings,
    )
    preorders_count = await _safe_count(
        db,
        select(func.count(Preorder.id)).where(Preorder.company_id == company_id),
        section="preorders",
        warnings=warnings,
    )
    campaigns_count = await _safe_count(
        db,
        select(func.count(Campaign.id)).where(Campaign.company_id == company_id, Campaign.deleted_at.is_(None)),
        section="campaigns",
        warnings=warnings,
    )
    subscriptions_count = await _safe_count(
        db,
        select(func.count(Subscription.id)).where(
            Subscription.company_id == company_id, Subscription.deleted_at.is_(None)
        ),
        section="subscriptions",
        warnings=warnings,
    )
    billing_payments_count = await _safe_count(
        db,
        select(func.count(BillingPayment.id)).where(
            BillingPayment.company_id == company_id, BillingPayment.deleted_at.is_(None)
        ),
        section="billing_payments",
        warnings=warnings,
    )
    repricing_rules_count = await _safe_count(
        db,
        select(func.count(RepricingRule.id)).where(RepricingRule.company_id == company_id),
        section="repricing_rules",
        warnings=warnings,
    )
    warehouses_count = await _safe_count(
        db,
        select(func.count(Warehouse.id)).where(Warehouse.company_id == company_id),
        section="warehouses",
        warnings=warnings,
    )

    section_counts = {
        "company": 1,
        "users": users_count,
        "products": products_count,
        "orders": orders_count,
        "preorders": preorders_count,
        "campaigns": campaigns_count,
        "subscriptions": subscriptions_count,
        "billing_payments": billing_payments_count,
        "repricing_rules": repricing_rules_count,
        "warehouses": warehouses_count,
    }

    return TenantExportManifestOut(
        company_id=company.id,
        company_name=company.name,
        exported_at=datetime.now(UTC),
        exported_by=exported_by,
        export_scope_version=EXPORT_SCOPE_VERSION,
        included_sections=INCLUDED_SECTIONS,
        section_counts=section_counts,
        warnings=warnings,
        not_included=NOT_INCLUDED,
    )


__all__ = ["build_tenant_export_manifest", "EXPORT_SCOPE_VERSION"]
