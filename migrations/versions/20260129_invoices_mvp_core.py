"""Invoices MVP core

Revision ID: 20260129_invoices_mvp_core
Revises: 20260129_campaigns_tenant_title_unique
Create Date: 2026-01-29
"""

import sqlalchemy as sa
from alembic import op

try:
    from migrations.utils.pghelpers import safe_inspect
except Exception:  # pragma: no cover - fallback if utils import fails
    safe_inspect = None  # type: ignore

revision = "20260129_invoices_mvp_core"
down_revision = "20260129_campaigns_tenant_title_unique"
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

    if not insp or insp.has_table("invoices"):
        op.add_column("invoices", sa.Column("issued_at", sa.DateTime(timezone=True), nullable=True))
        op.add_column("invoices", sa.Column("voided_at", sa.DateTime(timezone=True), nullable=True))
        op.add_column("invoices", sa.Column("ledger_entry_id", sa.Integer(), nullable=True))
        op.add_column("invoices", sa.Column("payment_ref", sa.String(length=128), nullable=True))
        op.create_foreign_key(
            "fk__invoices__ledger_entry_id__wallet_transactions",
            "invoices",
            "wallet_transactions",
            ["ledger_entry_id"],
            ["id"],
            ondelete="SET NULL",
        )

        if not insp or _has_index(insp, "invoices", "ix__invoices__invoice_number"):
            try:
                op.drop_index("ix__invoices__invoice_number", table_name="invoices")
            except Exception:
                pass

        if not insp or not _has_index(insp, "invoices", "ux_invoices_company_number"):
            op.create_index(
                "ux_invoices_company_number",
                "invoices",
                ["company_id", "invoice_number"],
                unique=True,
            )

        if not insp or not _has_index(insp, "invoices", "ix_invoices_company_status_created"):
            op.create_index(
                "ix_invoices_company_status_created",
                "invoices",
                ["company_id", "status", "created_at"],
                unique=False,
            )

    if not insp or insp.has_table("wallet_transactions"):
        op.add_column("wallet_transactions", sa.Column("client_request_id", sa.String(length=128), nullable=True))
        if bind.dialect.name.lower().startswith("postgres"):
            op.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS ux_wallet_transactions_request_id "
                "ON wallet_transactions (wallet_id, client_request_id) WHERE client_request_id IS NOT NULL"
            )
        else:
            op.create_index(
                "ux_wallet_transactions_request_id",
                "wallet_transactions",
                ["wallet_id", "client_request_id"],
                unique=True,
            )


def downgrade() -> None:
    bind = op.get_bind()
    insp = safe_inspect(bind) if safe_inspect else None

    if not insp or insp.has_table("wallet_transactions"):
        if bind.dialect.name.lower().startswith("postgres"):
            op.execute("DROP INDEX IF EXISTS ux_wallet_transactions_request_id")
        else:
            try:
                op.drop_index("ux_wallet_transactions_request_id", table_name="wallet_transactions")
            except Exception:
                pass
        op.drop_column("wallet_transactions", "client_request_id")

    if not insp or insp.has_table("invoices"):
        try:
            op.drop_index("ix_invoices_company_status_created", table_name="invoices")
        except Exception:
            pass
        try:
            op.drop_index("ux_invoices_company_number", table_name="invoices")
        except Exception:
            pass

        op.drop_constraint(
            "fk__invoices__ledger_entry_id__wallet_transactions",
            "invoices",
            type_="foreignkey",
        )
        op.drop_column("invoices", "payment_ref")
        op.drop_column("invoices", "ledger_entry_id")
        op.drop_column("invoices", "voided_at")
        op.drop_column("invoices", "issued_at")

        if not insp or not _has_index(insp, "invoices", "ix__invoices__invoice_number"):
            op.create_index("ix__invoices__invoice_number", "invoices", ["invoice_number"], unique=True)