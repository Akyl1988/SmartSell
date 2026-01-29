"""Campaigns tenant title uniqueness

Revision ID: 20260129_campaigns_tenant_title_unique
Revises: 20260129_products_tenant_uniqs
Create Date: 2026-01-29
"""

from alembic import op

try:
    from migrations.utils.pghelpers import safe_inspect
except Exception:  # pragma: no cover - fallback if utils import fails
    safe_inspect = None  # type: ignore

revision = "20260129_campaigns_tenant_title_unique"
down_revision = "20260129_products_tenant_uniqs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = safe_inspect(bind) if safe_inspect else None

    if not insp or insp.has_table("campaigns"):
        if bind.dialect.name.lower().startswith("postgres"):
            op.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS ux_campaigns_company_title_active "
                "ON campaigns (company_id, lower(title)) WHERE deleted_at IS NULL"
            )
        else:
            op.create_index(
                "ux_campaigns_company_title_active",
                "campaigns",
                ["company_id", "title"],
                unique=True,
            )


def downgrade() -> None:
    bind = op.get_bind()
    insp = safe_inspect(bind) if safe_inspect else None

    if not insp or insp.has_table("campaigns"):
        if bind.dialect.name.lower().startswith("postgres"):
            op.execute("DROP INDEX IF EXISTS ux_campaigns_company_title_active")
        else:
            try:
                op.drop_index("ux_campaigns_company_title_active", table_name="campaigns")
            except Exception:
                pass