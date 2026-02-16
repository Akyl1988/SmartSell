"""Make KZT money integer-only

Revision ID: 20260217_kzt_integer_money
Revises: 20260216_plans_features_usage
Create Date: 2026-02-17
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

try:
    from migrations.utils.pghelpers import safe_inspect
except Exception:  # pragma: no cover - fallback if utils import fails
    safe_inspect = None  # type: ignore

revision = "20260217_kzt_integer_money"
down_revision = "20260216_plans_features_usage"
branch_labels = None
depends_on = None


def _has_table(insp, table: str) -> bool:
    try:
        return insp.has_table(table)
    except Exception:
        return False


def _has_column(insp, table: str, column: str) -> bool:
    try:
        return any(col.get("name") == column for col in insp.get_columns(table))
    except Exception:
        return False


def _alter_numeric_scale(
    *,
    table: str,
    column: str,
    existing: sa.Numeric,
    target: sa.Numeric,
    round_scale: int,
    dialect: str,
) -> None:
    kwargs: dict[str, object] = {
        "existing_type": existing,
        "type_": target,
    }
    if dialect == "postgresql":
        kwargs["postgresql_using"] = f"ROUND({column}, {round_scale})"
    op.alter_column(table, column, **kwargs)


def upgrade() -> None:
    bind = op.get_bind()
    insp = safe_inspect(bind) if safe_inspect else None
    dialect = (bind.dialect.name or "").lower()

    if not insp:
        return

    if _has_table(insp, "subscriptions") and _has_column(insp, "subscriptions", "price"):
        _alter_numeric_scale(
            table="subscriptions",
            column="price",
            existing=sa.Numeric(14, 2),
            target=sa.Numeric(14, 0),
            round_scale=0,
            dialect=dialect,
        )

    if _has_table(insp, "plans") and _has_column(insp, "plans", "price"):
        _alter_numeric_scale(
            table="plans",
            column="price",
            existing=sa.Numeric(14, 2),
            target=sa.Numeric(14, 0),
            round_scale=0,
            dialect=dialect,
        )

    if _has_table(insp, "wallet_balances"):
        for col in ("balance", "credit_limit", "auto_topup_threshold", "auto_topup_amount"):
            if _has_column(insp, "wallet_balances", col):
                _alter_numeric_scale(
                    table="wallet_balances",
                    column=col,
                    existing=sa.Numeric(14, 2),
                    target=sa.Numeric(14, 0),
                    round_scale=0,
                    dialect=dialect,
                )

    if _has_table(insp, "wallet_transactions"):
        for col in ("amount", "balance_before", "balance_after"):
            if _has_column(insp, "wallet_transactions", col):
                _alter_numeric_scale(
                    table="wallet_transactions",
                    column=col,
                    existing=sa.Numeric(14, 2),
                    target=sa.Numeric(14, 0),
                    round_scale=0,
                    dialect=dialect,
                )



def downgrade() -> None:
    bind = op.get_bind()
    insp = safe_inspect(bind) if safe_inspect else None
    dialect = (bind.dialect.name or "").lower()

    if not insp:
        return

    if _has_table(insp, "subscriptions") and _has_column(insp, "subscriptions", "price"):
        _alter_numeric_scale(
            table="subscriptions",
            column="price",
            existing=sa.Numeric(14, 0),
            target=sa.Numeric(14, 2),
            round_scale=2,
            dialect=dialect,
        )

    if _has_table(insp, "plans") and _has_column(insp, "plans", "price"):
        _alter_numeric_scale(
            table="plans",
            column="price",
            existing=sa.Numeric(14, 0),
            target=sa.Numeric(14, 2),
            round_scale=2,
            dialect=dialect,
        )

    if _has_table(insp, "wallet_balances"):
        for col in ("balance", "credit_limit", "auto_topup_threshold", "auto_topup_amount"):
            if _has_column(insp, "wallet_balances", col):
                _alter_numeric_scale(
                    table="wallet_balances",
                    column=col,
                    existing=sa.Numeric(14, 0),
                    target=sa.Numeric(14, 2),
                    round_scale=2,
                    dialect=dialect,
                )

    if _has_table(insp, "wallet_transactions"):
        for col in ("amount", "balance_before", "balance_after"):
            if _has_column(insp, "wallet_transactions", col):
                _alter_numeric_scale(
                    table="wallet_transactions",
                    column=col,
                    existing=sa.Numeric(14, 0),
                    target=sa.Numeric(14, 2),
                    round_scale=2,
                    dialect=dialect,
                )

