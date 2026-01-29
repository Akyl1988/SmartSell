"""Products tenant-level uniqueness

Revision ID: 20260129_products_tenant_uniqs
Revises: 20260129_subscription_enforcement_fields
Create Date: 2026-01-29
"""

import sqlalchemy as sa
from alembic import op

try:
    from migrations.utils.pghelpers import safe_inspect
except Exception:  # pragma: no cover - fallback if utils import fails
    safe_inspect = None  # type: ignore

revision = "20260129_products_tenant_uniqs"
down_revision = "20260129_subscription_enforcement_fields"
branch_labels = None
depends_on = None


def _has_index(insp, table: str, name: str) -> bool:
    try:
        return any(idx.get("name") == name for idx in insp.get_indexes(table))
    except Exception:
        return False


def upgrade() -> None:
    bind = op.get_bind()
    insp = safe_inspect(bind) if safe_inspect else None

    if not insp or insp.has_table("products"):
        if not insp or not _has_index(insp, "products", "ux_products_company_sku"):
            op.create_index(
                "ux_products_company_sku",
                "products",
                ["company_id", "sku"],
                unique=True,
                postgresql_where=sa.text("sku IS NOT NULL"),
            )
        if not insp or not _has_index(insp, "products", "ux_products_company_slug"):
            op.create_index(
                "ux_products_company_slug",
                "products",
                ["company_id", "slug"],
                unique=True,
                postgresql_where=sa.text("slug IS NOT NULL"),
            )


def downgrade() -> None:
    bind = op.get_bind()
    insp = safe_inspect(bind) if safe_inspect else None

    if not insp or insp.has_table("products"):
        for idx in ("ux_products_company_slug", "ux_products_company_sku"):
            try:
                op.drop_index(idx, table_name="products")
            except Exception:
                pass
