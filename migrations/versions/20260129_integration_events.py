"""Integration events log

Revision ID: 20260129_integration_events
Revises: 20260129_invoices_mvp_core
Create Date: 2026-01-29
"""

import sqlalchemy as sa
from alembic import op

try:
    from migrations.utils.pghelpers import safe_inspect
except Exception:  # pragma: no cover - fallback if utils import fails
    safe_inspect = None  # type: ignore

revision = "20260129_integration_events"
down_revision = "20260129_invoices_mvp_core"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = safe_inspect(bind) if safe_inspect else None

    if not insp or insp.has_table("kaspi_store_tokens"):
        op.add_column(
            "kaspi_store_tokens",
            sa.Column("last_selftest_at", sa.DateTime(timezone=True), nullable=True),
        )
        op.add_column(
            "kaspi_store_tokens",
            sa.Column("last_selftest_status", sa.String(length=64), nullable=True),
        )
        op.add_column(
            "kaspi_store_tokens",
            sa.Column("last_selftest_error_code", sa.String(length=64), nullable=True),
        )
        op.add_column(
            "kaspi_store_tokens",
            sa.Column("last_selftest_error_message", sa.String(length=500), nullable=True),
        )

    if not insp or not insp.has_table("integration_events"):
        op.create_table(
            "integration_events",
            sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
            sa.Column("company_id", sa.BigInteger(), sa.ForeignKey("companies.id"), nullable=False),
            sa.Column("merchant_uid", sa.String(length=128), nullable=True),
            sa.Column("kind", sa.String(length=64), nullable=False),
            sa.Column("status", sa.String(length=64), nullable=False),
            sa.Column("error_code", sa.String(length=64), nullable=True),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("request_id", sa.String(length=128), nullable=True),
            sa.Column("occurred_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("meta_json", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
        )
        op.create_index(
            "ix_integration_events_company_kind_occurred",
            "integration_events",
            ["company_id", "kind", "occurred_at"],
            unique=False,
        )

    if bind.dialect.name.lower().startswith("postgres"):
        op.execute(
            "ALTER TABLE integration_events "
            "ALTER COLUMN meta_json TYPE JSONB USING meta_json::jsonb"
        )


def downgrade() -> None:
    bind = op.get_bind()
    insp = safe_inspect(bind) if safe_inspect else None

    if not insp or insp.has_table("kaspi_store_tokens"):
        try:
            op.drop_column("kaspi_store_tokens", "last_selftest_error_message")
        except Exception:
            pass
        try:
            op.drop_column("kaspi_store_tokens", "last_selftest_error_code")
        except Exception:
            pass
        try:
            op.drop_column("kaspi_store_tokens", "last_selftest_status")
        except Exception:
            pass
        try:
            op.drop_column("kaspi_store_tokens", "last_selftest_at")
        except Exception:
            pass

    if not insp or insp.has_table("integration_events"):
        try:
            op.drop_index("ix_integration_events_company_kind_occurred", table_name="integration_events")
        except Exception:
            pass
        op.drop_table("integration_events")
