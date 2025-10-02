"""add_comprehensive_fastapi_models_and_relationships

Revision ID: 1f107bc8bf2e
Revises: 2657f8ff8aa1
Create Date: 2025-09-15 14:11:25.485134
"""

from __future__ import annotations

from typing import Sequence, Union, Iterable

import os
import sqlalchemy as sa
from sqlalchemy import inspect, text

from alembic import op

# --- Alembic identifiers ---
revision: str = "1f107bc8bf2e"
down_revision: Union[str, Sequence[str], None] = "2657f8ff8aa1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# ============================================================
#                Общие настройки и утилиты
# ============================================================

# Стараемся уважать схему, если её прокинули через env.py / -x schema=... / переменные окружения.
# env.py уже делает SET search_path, но тут мы подхватим значение для information_schema-запросов.
SCHEMA = os.getenv("DB_SCHEMA", "public").strip() or "public"


def _insp_bind():
    bind = op.get_bind()
    return inspect(bind), bind


def _norm_schema(schema: str | None) -> str:
    return (schema or SCHEMA or "public").strip() or "public"


# ---------------------------
# Helpers (idempotent & safe)
# ---------------------------

def table_exists(table_name: str, schema: str | None = None) -> bool:
    schema = _norm_schema(schema)
    insp, _ = _insp_bind()
    return table_name in insp.get_table_names(schema=schema)


def column_exists(table_name: str, column_name: str, schema: str | None = None) -> bool:
    schema = _norm_schema(schema)
    insp, _ = _insp_bind()
    if not table_exists(table_name, schema):
        return False
    return any(col["name"] == column_name for col in insp.get_columns(table_name, schema=schema))


def _all_columns_exist(table: str, columns: Iterable[str], schema: str | None = None) -> bool:
    schema = _norm_schema(schema)
    return all(column_exists(table, c, schema=schema) for c in columns)


def index_exists(table_name: str, index_name: str, schema: str | None = None) -> bool:
    schema = _norm_schema(schema)
    insp, _ = _insp_bind()
    if not table_exists(table_name, schema):
        return False
    return any(ix["name"] == index_name for ix in insp.get_indexes(table_name, schema=schema))


def unique_constraint_exists(
    table_name: str, constraint_name: str, schema: str | None = None
) -> bool:
    schema = _norm_schema(schema)
    insp, _ = _insp_bind()
    if not table_exists(table_name, schema):
        return False
    return any(
        uc["name"] == constraint_name
        for uc in insp.get_unique_constraints(table_name, schema=schema)
    )


def get_unique_constraints(table_name: str, schema: str | None = None):
    schema = _norm_schema(schema)
    insp, _ = _insp_bind()
    if not table_exists(table_name, schema):
        return []
    return insp.get_unique_constraints(table_name, schema=schema)


def has_unique_on_columns(table_name: str, columns: list[str], schema: str | None = None) -> bool:
    schema = _norm_schema(schema)
    cols_norm = [c.lower() for c in columns]
    insp, _ = _insp_bind()

    # Уникальные ограничения
    for uc in get_unique_constraints(table_name, schema):
        uc_cols = [c.lower() for c in uc.get("column_names", [])]
        if uc_cols == cols_norm:
            return True

    # Уникальные индексы
    if table_exists(table_name, schema):
        for ix in insp.get_indexes(table_name, schema=schema):
            if ix.get("unique"):
                ix_cols = [c.lower() for c in ix.get("column_names", [])]
                if ix_cols == cols_norm:
                    return True
    return False


def fk_exists(table_name: str, fk_name: str, schema: str | None = None) -> bool:
    schema = _norm_schema(schema)
    insp, _ = _insp_bind()
    if not table_exists(table_name, schema):
        return False
    return any(fk.get("name") == fk_name for fk in insp.get_foreign_keys(table_name, schema=schema))


def create_index_safe(
    index_name: str, table_name: str, columns: list[str], unique: bool = False, schema: str | None = None
) -> None:
    """
    Создаёт индекс, только если:
      - существует таблица,
      - существуют ВСЕ перечисленные колонки,
      - индекс с таким именем ещё не существует.
    """
    schema = _norm_schema(schema)
    if table_exists(table_name, schema) and _all_columns_exist(table_name, columns, schema) and not index_exists(table_name, index_name, schema):
        op.create_index(index_name, table_name, columns, unique=unique)


def drop_index_if_exists(index_name: str, table_name: str, schema: str | None = None) -> None:
    schema = _norm_schema(schema)
    if table_exists(table_name, schema) and index_exists(table_name, index_name, schema):
        op.drop_index(index_name, table_name=table_name)


def add_column_safe(table_name: str, column: sa.Column, schema: str | None = None) -> None:
    schema = _norm_schema(schema)
    if table_exists(table_name, schema) and not column_exists(table_name, column.name, schema):
        op.add_column(table_name, column)


def drop_constraint_if_exists(table_name: str, constraint_name: str, type_: str, schema: str | None = None) -> None:
    schema = _norm_schema(schema)
    if table_exists(table_name, schema) and unique_constraint_exists(table_name, constraint_name, schema):
        op.drop_constraint(constraint_name, table_name, type_=type_)


def drop_unique_on_column_if_any(table_name: str, column_name: str, schema: str | None = None) -> None:
    """Снимает любые unique (constraint или index) на один столбец, если совпадает набор колонок."""
    schema = _norm_schema(schema)
    if not table_exists(table_name, schema):
        return
    insp, _ = _insp_bind()
    # 1) Уникальные ограничения
    for uc in insp.get_unique_constraints(table_name, schema=schema):
        cols = [c.lower() for c in (uc.get("column_names") or [])]
        if cols == [column_name.lower()]:
            op.drop_constraint(uc["name"], table_name, type_="unique")
    # 2) Уникальные индексы
    for ix in insp.get_indexes(table_name, schema=schema):
        cols = [c.lower() for c in (ix.get("column_names") or [])]
        if ix.get("unique") and cols == [column_name.lower()]:
            op.drop_index(ix["name"], table_name=table_name)


def create_unique_constraint_safe(name: str, table_name: str, columns: list[str], schema: str | None = None) -> None:
    schema = _norm_schema(schema)
    if not table_exists(table_name, schema):
        return
    if unique_constraint_exists(table_name, name, schema):
        return
    if has_unique_on_columns(table_name, columns, schema):
        return
    op.create_unique_constraint(name, table_name, columns)


def create_fk_not_valid_safe(
    fk_name: str,
    source_table: str,
    referent_table: str,
    local_cols: list[str],
    remote_cols: list[str],
    ondelete: str | None = None,
    schema: str | None = None,
) -> None:
    """Создаёт FK как NOT VALID (не валидируем существующие данные), если не существует."""
    schema = _norm_schema(schema)
    if not (table_exists(source_table, schema) and table_exists(referent_table, schema)):
        return
    if fk_exists(source_table, fk_name, schema):
        return
    _, bind = _insp_bind()
    ondelete_sql = f" ON DELETE {ondelete}" if ondelete else ""
    # Схему не указываем явно у таблиц — env.py уже делает SET search_path=SCHEMA.
    sql = f"""
        ALTER TABLE ONLY {source_table}
        ADD CONSTRAINT {fk_name}
        FOREIGN KEY ({', '.join(local_cols)}) REFERENCES {referent_table} ({', '.join(remote_cols)})
        {ondelete_sql} NOT VALID;
    """
    bind.execute(text(sql))


def validate_fk_safe(table_name: str, fk_name: str, schema: str | None = None) -> None:
    """Пытается VALIDATE CONSTRAINT; если есть плохие данные — пропускаем без ошибки."""
    schema = _norm_schema(schema)
    if not (table_exists(table_name, schema) and fk_exists(table_name, fk_name, schema)):
        return
    _, bind = _insp_bind()
    try:
        bind.execute(text(f"ALTER TABLE {table_name} VALIDATE CONSTRAINT {fk_name};"))
    except Exception:
        # Есть нарушения — оставим NOT VALID, логика приложения может починить позже.
        pass


# ============================================================
#                    Upgrade / Downgrade
# ============================================================

def upgrade() -> None:
    """Add comprehensive FastAPI models and relationships (idempotent & safe)."""

    # --- USERS: добавляем multi-tenant поля ---
    if table_exists("users", SCHEMA):
        add_column_safe("users", sa.Column("company_id", sa.Integer(), nullable=True), SCHEMA)
        add_column_safe(
            "users",
            sa.Column("role", sa.String(32), nullable=False, server_default=sa.text("'manager'")),
            SCHEMA,
        )
        create_index_safe("ix_users_company_id", "users", ["company_id"], schema=SCHEMA)
        # FK (NOT VALID, чтобы не падать на старых данных)
        create_fk_not_valid_safe(
            "fk_users_company_id",
            "users",
            "companies",
            local_cols=["company_id"],
            remote_cols=["id"],
            ondelete="CASCADE",
            schema=SCHEMA,
        )
        validate_fk_safe("users", "fk_users_company_id", SCHEMA)

    # --- PRODUCTS: расширяем схему и меняем уникальности на (company_id, ...) ---
    if table_exists("products", SCHEMA):
        # новые/нужные колонки
        add_column_safe("products", sa.Column("company_id", sa.Integer(), nullable=True), SCHEMA)
        add_column_safe("products", sa.Column("min_price", sa.Numeric(14, 2)), SCHEMA)
        add_column_safe("products", sa.Column("max_price", sa.Numeric(14, 2)), SCHEMA)
        add_column_safe(
            "products",
            sa.Column(
                "is_preorder_enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")
            ),
            SCHEMA,
        )
        add_column_safe("products", sa.Column("preorder_until", sa.Integer()), SCHEMA)
        add_column_safe("products", sa.Column("preorder_deposit", sa.Numeric(14, 2)), SCHEMA)
        add_column_safe("products", sa.Column("preorder_note", sa.String(500)), SCHEMA)
        add_column_safe(
            "products",
            sa.Column(
                "enable_price_dumping",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("false"),
            ),
            SCHEMA,
        )
        add_column_safe(
            "products",
            sa.Column(
                "exclude_friendly_stores",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("true"),
            ),
            SCHEMA,
        )
        add_column_safe("products", sa.Column("image_public_id", sa.String(255)), SCHEMA)
        add_column_safe("products", sa.Column("kaspi_product_id", sa.String(64)), SCHEMA)
        add_column_safe("products", sa.Column("kaspi_status", sa.String(32)), SCHEMA)
        # На всякий случай: если slug нет в схеме — создаём (требуется для уникальности ниже)
        add_column_safe("products", sa.Column("slug", sa.String(255)), SCHEMA)

        create_index_safe("ix_products_company_id", "products", ["company_id"], schema=SCHEMA)
        create_index_safe(
            "ix_products_kaspi_product_id", "products", ["kaspi_product_id"], schema=SCHEMA
        )

        # Снять старые уникальные ограничения на sku/slug (если были одиночные)
        # 1) Явные имена из старых миграций
        for name in ["uq_product_sku", "uq_product_slug", "uq_products_sku", "uq_products_slug"]:
            drop_constraint_if_exists("products", name, type_="unique", schema=SCHEMA)
        # 2) Дефолтные имена от Postgres/SQLAlchemy
        for name in ["products_sku_key", "products_slug_key"]:
            drop_constraint_if_exists("products", name, type_="unique", schema=SCHEMA)
        # 3) Если кто-то создал unique-индексы вместо constraints — тоже снимем
        drop_index_if_exists("uq_product_sku", "products", SCHEMA)
        drop_index_if_exists("uq_product_slug", "products", SCHEMA)

        # Создаём составные уникальные (company_id, sku) и (company_id, slug)
        if column_exists("products", "company_id", SCHEMA) and column_exists("products", "sku", SCHEMA):
            create_unique_constraint_safe(
                "uq_product_company_sku", "products", ["company_id", "sku"], SCHEMA
            )
        if column_exists("products", "company_id", SCHEMA) and column_exists("products", "slug", SCHEMA):
            create_unique_constraint_safe(
                "uq_product_company_slug", "products", ["company_id", "slug"], SCHEMA
            )

        # FK products.company_id → companies.id (NOT VALID)
        create_fk_not_valid_safe(
            "fk_products_company_id",
            "products",
            "companies",
            local_cols=["company_id"],
            remote_cols=["id"],
            ondelete="CASCADE",
            schema=SCHEMA,
        )
        validate_fk_safe("products", "fk_products_company_id", SCHEMA)

    # -----------------
    # Billing: подписки
    # -----------------
    if not table_exists("subscriptions", SCHEMA):
        op.create_table(
            "subscriptions",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("company_id", sa.Integer(), nullable=False),
            sa.Column("plan", sa.String(32), nullable=False),
            sa.Column("status", sa.String(32), nullable=False),
            sa.Column(
                "billing_cycle", sa.String(32), nullable=False, server_default=sa.text("'monthly'")
            ),
            sa.Column("price", sa.Numeric(10, 2), nullable=False),
            sa.Column("currency", sa.String(8), nullable=False, server_default=sa.text("'KZT'")),
            sa.Column("started_at", sa.DateTime(timezone=True)),
            sa.Column("expires_at", sa.DateTime(timezone=True)),
            sa.Column("canceled_at", sa.DateTime(timezone=True)),
            sa.Column("last_payment_id", sa.Integer()),
            sa.Column("next_billing_date", sa.DateTime(timezone=True)),
            sa.Column("auto_renew", sa.Boolean(), nullable=False, server_default=sa.text("true")),
            sa.Column("trial_used", sa.Boolean(), nullable=False, server_default=sa.text("false")),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.PrimaryKeyConstraint("id"),
        )
    create_index_safe("ix_subscriptions_id", "subscriptions", ["id"], schema=SCHEMA)
    create_index_safe("ix_subscriptions_company_id", "subscriptions", ["company_id"], schema=SCHEMA)
    create_index_safe("ix_subscriptions_plan", "subscriptions", ["plan"], schema=SCHEMA)
    create_index_safe("ix_subscriptions_status", "subscriptions", ["status"], schema=SCHEMA)
    create_index_safe(
        "ix_subscriptions_expires_at", "subscriptions", ["expires_at"], schema=SCHEMA
    )
    create_index_safe(
        "ix_subscriptions_next_billing_date",
        "subscriptions",
        ["next_billing_date"],
        schema=SCHEMA,
    )
    # FK company_id
    create_fk_not_valid_safe(
        "fk_subscriptions_company_id",
        "subscriptions",
        "companies",
        local_cols=["company_id"],
        remote_cols=["id"],
        ondelete="CASCADE",
        schema=SCHEMA,
    )
    validate_fk_safe("subscriptions", "fk_subscriptions_company_id", SCHEMA)

    # -----------------------
    # Платежи биллинга
    # -----------------------
    if not table_exists("billing_payments", SCHEMA):
        op.create_table(
            "billing_payments",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("company_id", sa.Integer(), nullable=False),
            sa.Column("subscription_id", sa.Integer(), nullable=True),
            sa.Column("amount", sa.Numeric(10, 2), nullable=False),
            sa.Column("currency", sa.String(8), nullable=False, server_default=sa.text("'KZT'")),
            sa.Column("payment_type", sa.String(32), nullable=False),
            sa.Column("provider", sa.String(32), nullable=False, server_default=sa.text("'tiptop'")),
            sa.Column("provider_invoice_id", sa.String(128), nullable=False),
            sa.Column("provider_transaction_id", sa.String(128)),
            sa.Column("status", sa.String(32), nullable=False),
            sa.Column("processed_at", sa.DateTime(timezone=True)),
            sa.Column("description", sa.String(255)),
            sa.Column("billing_period_start", sa.DateTime(timezone=True)),
            sa.Column("billing_period_end", sa.DateTime(timezone=True)),
            sa.Column("receipt_url", sa.String(1024)),
            sa.Column("receipt_number", sa.String(64)),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("provider_invoice_id"),
        )
    # Индексы — только если колонка точно существует
    create_index_safe("ix_billing_payments_id", "billing_payments", ["id"], schema=SCHEMA)
    create_index_safe(
        "ix_billing_payments_company_id", "billing_payments", ["company_id"], schema=SCHEMA
    )
    create_index_safe(
        "ix_billing_payments_subscription_id",
        "billing_payments",
        ["subscription_id"],
        schema=SCHEMA,
    )
    create_index_safe(
        "ix_billing_payments_payment_type", "billing_payments", ["payment_type"], schema=SCHEMA
    )
    create_index_safe(
        "ix_billing_payments_provider_invoice_id",
        "billing_payments",
        ["provider_invoice_id"],
        schema=SCHEMA,
    )
    create_index_safe("ix_billing_payments_status", "billing_payments", ["status"], schema=SCHEMA)

    # FKs
    create_fk_not_valid_safe(
        "fk_billing_payments_company_id",
        "billing_payments",
        "companies",
        local_cols=["company_id"],
        remote_cols=["id"],
        ondelete="CASCADE",
        schema=SCHEMA,
    )
    validate_fk_safe("billing_payments", "fk_billing_payments_company_id", SCHEMA)

    create_fk_not_valid_safe(
        "fk_billing_payments_subscription_id",
        "billing_payments",
        "subscriptions",
        local_cols=["subscription_id"],
        remote_cols=["id"],
        ondelete="CASCADE",
        schema=SCHEMA,
    )
    validate_fk_safe("billing_payments", "fk_billing_payments_subscription_id", SCHEMA)

    # Back-ref: subscriptions.last_payment_id → billing_payments.id
    if table_exists("subscriptions", SCHEMA) and column_exists("subscriptions", "last_payment_id", SCHEMA):
        create_fk_not_valid_safe(
            "fk_subscriptions_last_payment_id",
            "subscriptions",
            "billing_payments",
            local_cols=["last_payment_id"],
            remote_cols=["id"],
            schema=SCHEMA,
        )
        validate_fk_safe("subscriptions", "fk_subscriptions_last_payment_id", SCHEMA)

    # -------------
    # Invoices
    # -------------
    if not table_exists("invoices", SCHEMA):
        op.create_table(
            "invoices",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("order_id", sa.Integer()),
            sa.Column("company_id", sa.Integer(), nullable=False),
            sa.Column("invoice_number", sa.String(64), nullable=False),
            sa.Column("invoice_type", sa.String(32), nullable=False),
            sa.Column("subtotal", sa.Numeric(10, 2), nullable=False),
            sa.Column("tax_amount", sa.Numeric(10, 2), nullable=True, server_default=sa.text("0")),
            sa.Column("total_amount", sa.Numeric(10, 2), nullable=False),
            sa.Column("currency", sa.String(8), nullable=False, server_default=sa.text("'KZT'")),
            sa.Column("status", sa.String(32), nullable=False),
            sa.Column("issue_date", sa.DateTime(timezone=True), nullable=False),
            sa.Column("due_date", sa.DateTime(timezone=True)),
            sa.Column("paid_at", sa.DateTime(timezone=True)),
            sa.Column("pdf_url", sa.String(1024)),
            sa.Column("pdf_path", sa.String(512)),
            sa.Column("notes", sa.Text()),
            sa.Column("internal_notes", sa.Text()),
            sa.Column("payment_id", sa.Integer()),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("invoice_number"),
        )
    create_index_safe("ix_invoices_id", "invoices", ["id"], schema=SCHEMA)
    create_index_safe("ix_invoices_order_id", "invoices", ["order_id"], schema=SCHEMA)
    create_index_safe("ix_invoices_company_id", "invoices", ["company_id"], schema=SCHEMA)
    create_index_safe("ix_invoices_invoice_number", "invoices", ["invoice_number"], schema=SCHEMA)
    create_index_safe("ix_invoices_invoice_type", "invoices", ["invoice_type"], schema=SCHEMA)
    create_index_safe("ix_invoices_status", "invoices", ["status"], schema=SCHEMA)

    create_fk_not_valid_safe(
        "fk_invoices_company_id",
        "invoices",
        "companies",
        local_cols=["company_id"],
        remote_cols=["id"],
        ondelete="CASCADE",
        schema=SCHEMA,
    )
    validate_fk_safe("invoices", "fk_invoices_company_id", SCHEMA)

    create_fk_not_valid_safe(
        "fk_invoices_payment_id",
        "invoices",
        "billing_payments",
        local_cols=["payment_id"],
        remote_cols=["id"],
        schema=SCHEMA,
    )
    validate_fk_safe("invoices", "fk_invoices_payment_id", SCHEMA)

    # ----------------
    # Wallet balances
    # ----------------
    if not table_exists("wallet_balances", SCHEMA):
        op.create_table(
            "wallet_balances",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("company_id", sa.Integer(), nullable=False),
            sa.Column("balance", sa.Numeric(10, 2), nullable=False, server_default=sa.text("0")),
            sa.Column("currency", sa.String(8), nullable=False, server_default=sa.text("'KZT'")),
            sa.Column(
                "credit_limit", sa.Numeric(10, 2), nullable=True, server_default=sa.text("0")
            ),
            sa.Column(
                "auto_topup_enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")
            ),
            sa.Column("auto_topup_threshold", sa.Numeric(10, 2)),
            sa.Column("auto_topup_amount", sa.Numeric(10, 2)),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("company_id"),
        )
    create_index_safe("ix_wallet_balances_id", "wallet_balances", ["id"], schema=SCHEMA)
    create_index_safe(
        "ix_wallet_balances_company_id", "wallet_balances", ["company_id"], schema=SCHEMA
    )
    create_fk_not_valid_safe(
        "fk_wallet_balances_company_id",
        "wallet_balances",
        "companies",
        local_cols=["company_id"],
        remote_cols=["id"],
        ondelete="CASCADE",
        schema=SCHEMA,
    )
    validate_fk_safe("wallet_balances", "fk_wallet_balances_company_id", SCHEMA)

    # --------------------
    # Wallet transactions
    # --------------------
    if not table_exists("wallet_transactions", SCHEMA):
        op.create_table(
            "wallet_transactions",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("wallet_id", sa.Integer(), nullable=False),
            sa.Column("transaction_type", sa.String(32), nullable=False),
            sa.Column("amount", sa.Numeric(10, 2), nullable=False),
            sa.Column("balance_before", sa.Numeric(10, 2), nullable=False),
            sa.Column("balance_after", sa.Numeric(10, 2), nullable=False),
            sa.Column("reference_type", sa.String(32)),
            sa.Column("reference_id", sa.Integer()),
            sa.Column("description", sa.String(255), nullable=False),
            sa.Column("extra_data", sa.Text()),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.PrimaryKeyConstraint("id"),
        )
    create_index_safe("ix_wallet_transactions_id", "wallet_transactions", ["id"], schema=SCHEMA)
    create_index_safe(
        "ix_wallet_transactions_wallet_id", "wallet_transactions", ["wallet_id"], schema=SCHEMA
    )
    create_index_safe(
        "ix_wallet_transactions_transaction_type",
        "wallet_transactions",
        ["transaction_type"],
        schema=SCHEMA,
    )
    create_index_safe(
        "ix_wallet_transactions_reference_id",
        "wallet_transactions",
        ["reference_id"],
        schema=SCHEMA,
    )

    create_fk_not_valid_safe(
        "fk_wallet_transactions_wallet_id",
        "wallet_transactions",
        "wallet_balances",
        local_cols=["wallet_id"],
        remote_cols=["id"],
        ondelete="CASCADE",
        schema=SCHEMA,
    )
    validate_fk_safe("wallet_transactions", "fk_wallet_transactions_wallet_id", SCHEMA)

    # -------------
    # Bot sessions
    # -------------
    if not table_exists("bot_sessions", SCHEMA):
        op.create_table(
            "bot_sessions",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("company_id", sa.Integer(), nullable=False),
            sa.Column("session_type", sa.String(32), nullable=False),
            sa.Column("status", sa.String(32), nullable=False),
            sa.Column("context", sa.JSON()),
            sa.Column("intent", sa.String(64)),
            sa.Column("title", sa.String(255)),
            sa.Column("language", sa.String(8), nullable=False, server_default=sa.text("'ru'")),
            sa.Column("last_activity_at", sa.DateTime(timezone=True)),
            sa.Column("completed_at", sa.DateTime(timezone=True)),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.PrimaryKeyConstraint("id"),
        )
    create_index_safe("ix_bot_sessions_id", "bot_sessions", ["id"], schema=SCHEMA)
    create_index_safe("ix_bot_sessions_user_id", "bot_sessions", ["user_id"], schema=SCHEMA)
    create_index_safe("ix_bot_sessions_company_id", "bot_sessions", ["company_id"], schema=SCHEMA)
    create_index_safe(
        "ix_bot_sessions_session_type", "bot_sessions", ["session_type"], schema=SCHEMA
    )
    create_index_safe("ix_bot_sessions_status", "bot_sessions", ["status"], schema=SCHEMA)
    create_index_safe("ix_bot_sessions_intent", "bot_sessions", ["intent"], schema=SCHEMA)
    create_index_safe(
        "ix_bot_sessions_last_activity_at", "bot_sessions", ["last_activity_at"], schema=SCHEMA
    )

    create_fk_not_valid_safe(
        "fk_bot_sessions_user_id",
        "bot_sessions",
        "users",
        local_cols=["user_id"],
        remote_cols=["id"],
        ondelete="CASCADE",
        schema=SCHEMA,
    )
    validate_fk_safe("bot_sessions", "fk_bot_sessions_user_id", SCHEMA)

    create_fk_not_valid_safe(
        "fk_bot_sessions_company_id",
        "bot_sessions",
        "companies",
        local_cols=["company_id"],
        remote_cols=["id"],
        ondelete="CASCADE",
        schema=SCHEMA,
    )
    validate_fk_safe("bot_sessions", "fk_bot_sessions_company_id", SCHEMA)


def downgrade() -> None:
    """Safe revert: удаляем созданные объекты, если существуют, и возвращаем прежние уникальности."""

    # --- Bot sessions ---
    if table_exists("bot_sessions", SCHEMA):
        if fk_exists("bot_sessions", "fk_bot_sessions_user_id", SCHEMA):
            op.drop_constraint("fk_bot_sessions_user_id", "bot_sessions", type_="foreignkey")
        if fk_exists("bot_sessions", "fk_bot_sessions_company_id", SCHEMA):
            op.drop_constraint("fk_bot_sessions_company_id", "bot_sessions", type_="foreignkey")
        for name in [
            "ix_bot_sessions_last_activity_at",
            "ix_bot_sessions_intent",
            "ix_bot_sessions_status",
            "ix_bot_sessions_session_type",
            "ix_bot_sessions_company_id",
            "ix_bot_sessions_user_id",
            "ix_bot_sessions_id",
        ]:
            drop_index_if_exists(name, "bot_sessions", SCHEMA)
        op.drop_table("bot_sessions")

    # --- Wallet ---
    if table_exists("wallet_transactions", SCHEMA):
        if fk_exists("wallet_transactions", "fk_wallet_transactions_wallet_id", SCHEMA):
            op.drop_constraint("fk_wallet_transactions_wallet_id", "wallet_transactions", type_="foreignkey")
        for name in [
            "ix_wallet_transactions_reference_id",
            "ix_wallet_transactions_transaction_type",
            "ix_wallet_transactions_wallet_id",
            "ix_wallet_transactions_id",
        ]:
            drop_index_if_exists(name, "wallet_transactions", SCHEMA)
        op.drop_table("wallet_transactions")

    if table_exists("wallet_balances", SCHEMA):
        if fk_exists("wallet_balances", "fk_wallet_balances_company_id", SCHEMA):
            op.drop_constraint("fk_wallet_balances_company_id", "wallet_balances", type_="foreignkey")
        for name in [
            "ix_wallet_balances_company_id",
            "ix_wallet_balances_id",
        ]:
            drop_index_if_exists(name, "wallet_balances", SCHEMA)
        op.drop_table("wallet_balances")

    # --- Invoices ---
    if table_exists("invoices", SCHEMA):
        if fk_exists("invoices", "fk_invoices_company_id", SCHEMA):
            op.drop_constraint("fk_invoices_company_id", "invoices", type_="foreignkey")
        if fk_exists("invoices", "fk_invoices_payment_id", SCHEMA):
            op.drop_constraint("fk_invoices_payment_id", "invoices", type_="foreignkey")
        for name in [
            "ix_invoices_status",
            "ix_invoices_invoice_type",
            "ix_invoices_invoice_number",
            "ix_invoices_company_id",
            "ix_invoices_order_id",
            "ix_invoices_id",
        ]:
            drop_index_if_exists(name, "invoices", SCHEMA)
        op.drop_table("invoices")

    # --- Billing payments & subscriptions ---
    if table_exists("billing_payments", SCHEMA):
        if fk_exists("billing_payments", "fk_billing_payments_company_id", SCHEMA):
            op.drop_constraint("fk_billing_payments_company_id", "billing_payments", type_="foreignkey")
        if fk_exists("billing_payments", "fk_billing_payments_subscription_id", SCHEMA):
            op.drop_constraint("fk_billing_payments_subscription_id", "billing_payments", type_="foreignkey")
        for name in [
            "ix_billing_payments_status",
            "ix_billing_payments_provider_invoice_id",
            "ix_billing_payments_payment_type",
            "ix_billing_payments_subscription_id",
            "ix_billing_payments_company_id",
            "ix_billing_payments_id",
        ]:
            drop_index_if_exists(name, "billing_payments", SCHEMA)
        op.drop_table("billing_payments")

    if table_exists("subscriptions", SCHEMA):
        if fk_exists("subscriptions", "fk_subscriptions_last_payment_id", SCHEMA):
            op.drop_constraint("fk_subscriptions_last_payment_id", "subscriptions", type_="foreignkey")
        if fk_exists("subscriptions", "fk_subscriptions_company_id", SCHEMA):
            op.drop_constraint("fk_subscriptions_company_id", "subscriptions", type_="foreignkey")
        for name in [
            "ix_subscriptions_next_billing_date",
            "ix_subscriptions_expires_at",
            "ix_subscriptions_status",
            "ix_subscriptions_plan",
            "ix_subscriptions_company_id",
            "ix_subscriptions_id",
        ]:
            drop_index_if_exists(name, "subscriptions", SCHEMA)
        op.drop_table("subscriptions")

    # --- Products: возвращаем одиночные уникальности, убираем наши FK/индексы/колонки ---
    if table_exists("products", SCHEMA):
        # Сначала снять наши составные уникальности и FK
        if unique_constraint_exists("products", "uq_product_company_sku", SCHEMA):
            op.drop_constraint("uq_product_company_sku", "products", type_="unique")
        if unique_constraint_exists("products", "uq_product_company_slug", SCHEMA):
            op.drop_constraint("uq_product_company_slug", "products", type_="unique")
        if fk_exists("products", "fk_products_company_id", SCHEMA):
            op.drop_constraint("fk_products_company_id", "products", type_="foreignkey")

        # Индексы
        for name in ["ix_products_kaspi_product_id", "ix_products_company_id"]:
            drop_index_if_exists(name, "products", SCHEMA)

        # Удаляем добавленные колонки (в обратном порядке не принципиально)
        for col in [
            "kaspi_status",
            "kaspi_product_id",
            "image_public_id",
            "exclude_friendly_stores",
            "enable_price_dumping",
            "preorder_note",
            "preorder_deposit",
            "preorder_until",
            "is_preorder_enabled",
            "max_price",
            "min_price",
            "company_id",
            "slug",
        ]:
            if column_exists("products", col, SCHEMA):
                op.drop_column("products", col)

        # Восстанавливаем одиночные уникальности (если поля существуют)
        if column_exists("products", "sku", SCHEMA) and not has_unique_on_columns("products", ["sku"], SCHEMA):
            create_unique_constraint_safe("uq_product_sku", "products", ["sku"], SCHEMA)
        if column_exists("products", "slug", SCHEMA) and not has_unique_on_columns("products", ["slug"], SCHEMA):
            create_unique_constraint_safe("uq_product_slug", "products", ["slug"], SCHEMA)

    # --- Users: убираем наши FK/индекс/колонки ---
    if table_exists("users", SCHEMA):
        if fk_exists("users", "fk_users_company_id", SCHEMA):
            op.drop_constraint("fk_users_company_id", "users", type_="foreignkey")
        drop_index_if_exists("ix_users_company_id", "users", SCHEMA)
        for col in ["role", "company_id"]:
            if column_exists("users", col, SCHEMA):
                op.drop_column("users", col)
