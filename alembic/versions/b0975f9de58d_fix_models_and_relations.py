"""fix models and relations

Revision ID: b0975f9de58d
Revises: 1f107bc8bf2e
Create Date: 2025-09-20 00:24:23.515966
"""

from __future__ import annotations
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy import inspect
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b0975f9de58d"
down_revision: Union[str, Sequence[str], None] = "1f107bc8bf2e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# ---------------------------
# Helpers (idempotent & safe)
# ---------------------------


def _insp():
    bind = op.get_bind()
    return inspect(bind)


def table_exists(table_name: str) -> bool:
    return table_name in _insp().get_table_names()


def column_exists(table_name: str, column_name: str) -> bool:
    if not table_exists(table_name):
        return False
    return any(col["name"] == column_name for col in _insp().get_columns(table_name))


def index_exists(table_name: str, index_name: str) -> bool:
    if not table_exists(table_name):
        return False
    return any(ix["name"] == index_name for ix in _insp().get_indexes(table_name))


def constraint_exists(table_name: str, constraint_name: str, type_: str | None = None) -> bool:
    if not table_exists(table_name):
        return False
    insp = _insp()
    if type_ == "unique":
        return any(uc["name"] == constraint_name for uc in insp.get_unique_constraints(table_name))
    if type_ == "foreignkey":
        return any(fk["name"] == constraint_name for fk in insp.get_foreign_keys(table_name))
    return any(
        uc["name"] == constraint_name for uc in insp.get_unique_constraints(table_name)
    ) or any(fk["name"] == constraint_name for fk in insp.get_foreign_keys(table_name))


def create_index_safe(
    index_name: str, table_name: str, columns: list[str], unique: bool = False
) -> None:
    if table_exists(table_name) and not index_exists(table_name, index_name):
        op.create_index(index_name, table_name, columns, unique=unique)


def drop_index_if_exists(index_name: str, table_name: str) -> None:
    if index_exists(table_name, index_name):
        op.drop_index(index_name, table_name=table_name)


def add_column_safe(table_name: str, column: sa.Column) -> None:
    if table_exists(table_name) and not column_exists(table_name, column.name):
        op.add_column(table_name, column)


def create_fk_safe(
    fk_name: str | None,
    source_table: str,
    referent_table: str,
    local_cols: list[str],
    remote_cols: list[str],
    ondelete: str | None = None,
) -> None:
    if not (table_exists(source_table) and table_exists(referent_table)):
        return
    if fk_name and constraint_exists(source_table, fk_name, "foreignkey"):
        return
    op.create_foreign_key(
        fk_name, source_table, referent_table, local_cols, remote_cols, ondelete=ondelete
    )


def create_unique_constraint_safe(
    constraint_name: str, table_name: str, columns: list[str]
) -> None:
    if table_exists(table_name) and not constraint_exists(table_name, constraint_name, "unique"):
        op.create_unique_constraint(constraint_name, table_name, columns)


# ---------------------------
# Upgrade / Downgrade
# ---------------------------


def upgrade() -> None:
    """Дополняем схему поверх 1f107bc8bf2e: заказы, склады, оплаты заказов, остатки, возвраты, движения, мягкие расширения справочников."""

    # --- ORDERS ---
    if not table_exists("orders"):
        op.create_table(
            "orders",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("company_id", sa.Integer(), nullable=False),
            sa.Column("order_number", sa.String(64), nullable=False),
            sa.Column("external_id", sa.String(128)),
            sa.Column(
                "source",
                sa.Enum("KASPI", "WEBSITE", "MANUAL", "API", name="ordersource"),
                nullable=False,
            ),
            sa.Column(
                "status",
                sa.Enum(
                    "PENDING",
                    "CONFIRMED",
                    "PAID",
                    "PROCESSING",
                    "SHIPPED",
                    "DELIVERED",
                    "COMPLETED",
                    "CANCELLED",
                    "REFUNDED",
                    name="orderstatus",
                ),
                nullable=False,
            ),
            sa.Column("customer_phone", sa.String(32)),
            sa.Column("customer_email", sa.String(255)),
            sa.Column("customer_name", sa.String(255)),
            sa.Column("customer_address", sa.Text()),
            sa.Column("subtotal", sa.Numeric(14, 2), nullable=False),
            sa.Column("tax_amount", sa.Numeric(14, 2), nullable=False, server_default=sa.text("0")),
            sa.Column(
                "shipping_amount", sa.Numeric(14, 2), nullable=False, server_default=sa.text("0")
            ),
            sa.Column(
                "discount_amount", sa.Numeric(14, 2), nullable=False, server_default=sa.text("0")
            ),
            sa.Column("total_amount", sa.Numeric(14, 2), nullable=False),
            sa.Column("currency", sa.String(8), nullable=False, server_default=sa.text("'KZT'")),
            sa.Column("delivery_method", sa.String(64)),
            sa.Column("delivery_address", sa.Text()),
            sa.Column("delivery_date", sa.String(32)),
            sa.Column("delivery_time", sa.String(32)),
            sa.Column("notes", sa.Text()),
            sa.Column("internal_notes", sa.Text()),
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
            sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
        create_index_safe("ix_orders_company_id", "orders", ["company_id"])
        create_index_safe("ix_orders_customer_phone", "orders", ["customer_phone"])
        create_index_safe("ix_orders_external_id", "orders", ["external_id"])
        create_index_safe("ix_orders_order_number", "orders", ["order_number"], unique=True)
        create_index_safe("ix_orders_source", "orders", ["source"])
        create_index_safe("ix_orders_status", "orders", ["status"])

    # --- WAREHOUSES ---
    if not table_exists("warehouses"):
        op.create_table(
            "warehouses",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("company_id", sa.Integer(), nullable=False),
            sa.Column("name", sa.String(255), nullable=False),
            sa.Column("code", sa.String(32)),
            sa.Column("address", sa.Text()),
            sa.Column("city", sa.String(100)),
            sa.Column("region", sa.String(100)),
            sa.Column("postal_code", sa.String(20)),
            sa.Column("phone", sa.String(32)),
            sa.Column("email", sa.String(255)),
            sa.Column("manager_name", sa.String(255)),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
            sa.Column("is_main", sa.Boolean(), nullable=False, server_default=sa.text("false")),
            sa.Column("working_hours", sa.Text()),
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
            sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
        create_index_safe("ix_warehouses_company_id", "warehouses", ["company_id"])
        create_index_safe("ix_warehouses_code", "warehouses", ["code"])
        create_index_safe("ix_warehouses_is_active", "warehouses", ["is_active"])

    # --- ORDER ITEMS ---
    if not table_exists("order_items"):
        op.create_table(
            "order_items",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("order_id", sa.Integer(), nullable=False),
            sa.Column("product_id", sa.Integer()),
            sa.Column("sku", sa.String(64), nullable=False),
            sa.Column("name", sa.String(500), nullable=False),
            sa.Column("description", sa.Text()),
            sa.Column("unit_price", sa.Numeric(14, 2), nullable=False),
            sa.Column("quantity", sa.Integer(), nullable=False),
            sa.Column("total_price", sa.Numeric(14, 2), nullable=False),
            sa.Column("product_image_url", sa.String(1024)),
            sa.Column("notes", sa.Text()),
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
            sa.ForeignKeyConstraint(["order_id"], ["orders.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="RESTRICT"),
            sa.PrimaryKeyConstraint("id"),
        )
        create_index_safe("ix_order_items_order_id", "order_items", ["order_id"])
        create_index_safe("ix_order_items_product_id", "order_items", ["product_id"])

    # --- PAYMENTS (платежи по заказам; НЕ billing_payments) ---
    if not table_exists("payments"):
        op.create_table(
            "payments",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("order_id", sa.Integer(), nullable=False),
            sa.Column("payment_number", sa.String(64), nullable=False),
            sa.Column("external_id", sa.String(128)),
            sa.Column("provider_invoice_id", sa.String(128)),
            sa.Column(
                "provider",
                sa.Enum("TIPTOP", "KASPI", "PAYBOX", "MANUAL", name="paymentprovider"),
                nullable=False,
            ),
            sa.Column(
                "method",
                sa.Enum("CARD", "CASH", "BANK_TRANSFER", "WALLET", "QR_CODE", name="paymentmethod"),
                nullable=False,
            ),
            sa.Column(
                "status",
                sa.Enum(
                    "PENDING",
                    "PROCESSING",
                    "SUCCESS",
                    "FAILED",
                    "CANCELLED",
                    "REFUNDED",
                    "PARTIALLY_REFUNDED",
                    name="paymentstatus",
                ),
                nullable=False,
            ),
            sa.Column("amount", sa.Numeric(14, 2), nullable=False),
            sa.Column("fee_amount", sa.Numeric(14, 2), nullable=False, server_default=sa.text("0")),
            sa.Column(
                "refunded_amount", sa.Numeric(14, 2), nullable=False, server_default=sa.text("0")
            ),
            sa.Column("currency", sa.String(8), nullable=False, server_default=sa.text("'KZT'")),
            sa.Column("provider_data", sa.Text()),
            sa.Column("receipt_url", sa.String(1024)),
            sa.Column("receipt_number", sa.String(64)),
            sa.Column("processed_at", sa.DateTime(timezone=True)),
            sa.Column("confirmed_at", sa.DateTime(timezone=True)),
            sa.Column("failed_at", sa.DateTime(timezone=True)),
            sa.Column("description", sa.Text()),
            sa.Column("customer_ip", sa.String(45)),
            sa.Column("user_agent", sa.Text()),
            sa.Column("failure_reason", sa.String(255)),
            sa.Column("failure_code", sa.String(32)),
            sa.Column("is_test", sa.Boolean(), nullable=False, server_default=sa.text("false")),
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
            sa.ForeignKeyConstraint(["order_id"], ["orders.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
        create_index_safe("ix_payments_external_id", "payments", ["external_id"])
        create_index_safe("ix_payments_order_id", "payments", ["order_id"])
        create_index_safe("ix_payments_payment_number", "payments", ["payment_number"], unique=True)
        create_index_safe(
            "ix_payments_provider_invoice_id", "payments", ["provider_invoice_id"], unique=True
        )
        create_index_safe("ix_payments_status", "payments", ["status"])

    # --- PRODUCT STOCKS ---
    if not table_exists("product_stocks"):
        op.create_table(
            "product_stocks",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("product_id", sa.Integer(), nullable=False),
            sa.Column("warehouse_id", sa.Integer(), nullable=False),
            sa.Column("quantity", sa.Integer(), nullable=False, server_default=sa.text("0")),
            sa.Column(
                "reserved_quantity", sa.Integer(), nullable=False, server_default=sa.text("0")
            ),
            sa.Column("min_quantity", sa.Integer(), nullable=False, server_default=sa.text("0")),
            sa.Column("max_quantity", sa.Integer()),
            sa.Column("location", sa.String(100)),
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
            sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["warehouse_id"], ["warehouses.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("product_id", "warehouse_id", name="uq_product_warehouse"),
        )
        create_index_safe("ix_product_stocks_product_id", "product_stocks", ["product_id"])
        create_index_safe("ix_product_stocks_warehouse_id", "product_stocks", ["warehouse_id"])

    # --- PAYMENT REFUNDS ---
    if not table_exists("payment_refunds"):
        op.create_table(
            "payment_refunds",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("payment_id", sa.Integer(), nullable=False),
            sa.Column("refund_number", sa.String(64), nullable=False),
            sa.Column("external_id", sa.String(128)),
            sa.Column("amount", sa.Numeric(14, 2), nullable=False),
            sa.Column("currency", sa.String(8), nullable=False, server_default=sa.text("'KZT'")),
            sa.Column(
                "status",
                sa.Enum(
                    "PENDING",
                    "PROCESSING",
                    "SUCCESS",
                    "FAILED",
                    "CANCELLED",
                    "REFUNDED",
                    "PARTIALLY_REFUNDED",
                    name="paymentstatus",
                ),
                nullable=False,
            ),
            sa.Column("processed_at", sa.DateTime(timezone=True)),
            sa.Column("completed_at", sa.DateTime(timezone=True)),
            sa.Column("reason", sa.String(255)),
            sa.Column("notes", sa.Text()),
            sa.Column("provider_data", sa.Text()),
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
            sa.ForeignKeyConstraint(["payment_id"], ["payments.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
        create_index_safe("ix_payment_refunds_external_id", "payment_refunds", ["external_id"])
        create_index_safe("ix_payment_refunds_payment_id", "payment_refunds", ["payment_id"])
        create_index_safe(
            "ix_payment_refunds_refund_number", "payment_refunds", ["refund_number"], unique=True
        )
        create_index_safe("ix_payment_refunds_status", "payment_refunds", ["status"])

    # --- STOCK MOVEMENTS ---
    if not table_exists("stock_movements"):
        op.create_table(
            "stock_movements",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("stock_id", sa.Integer(), nullable=False),
            sa.Column("movement_type", sa.String(32), nullable=False),
            sa.Column("quantity", sa.Integer(), nullable=False),
            sa.Column("previous_quantity", sa.Integer(), nullable=False),
            sa.Column("new_quantity", sa.Integer(), nullable=False),
            sa.Column("reference_type", sa.String(32)),
            sa.Column("reference_id", sa.Integer()),
            sa.Column("reason", sa.String(255)),
            sa.Column("notes", sa.Text()),
            sa.Column("user_id", sa.Integer()),
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
            sa.ForeignKeyConstraint(["stock_id"], ["product_stocks.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("id"),
        )
        create_index_safe("ix_stock_movements_stock_id", "stock_movements", ["stock_id"])
        create_index_safe("ix_stock_movements_user_id", "stock_movements", ["user_id"])
        create_index_safe("ix_stock_movements_movement_type", "stock_movements", ["movement_type"])
        create_index_safe("ix_stock_movements_reference_id", "stock_movements", ["reference_id"])

    # --- AUDIT_LOGS: расширения (без удаления существующих полей) ---
    if table_exists("audit_logs"):
        add_column_safe("audit_logs", sa.Column("warehouse_id", sa.Integer()))
        add_column_safe("audit_logs", sa.Column("tenant_id", sa.Integer()))
        add_column_safe("audit_logs", sa.Column("last_modified_by", sa.Integer()))
        create_index_safe("ix_audit_logs_warehouse_id", "audit_logs", ["warehouse_id"])
        create_index_safe("ix_audit_logs_tenant_id", "audit_logs", ["tenant_id"])
        create_index_safe("ix_audit_logs_last_modified_by", "audit_logs", ["last_modified_by"])
        create_fk_safe(
            None, "audit_logs", "warehouses", ["warehouse_id"], ["id"], ondelete="SET NULL"
        )

    # --- COMPANIES: мягкие расширения ---
    if table_exists("companies"):
        add_column_safe("companies", sa.Column("deleted_at", sa.DateTime(timezone=True)))
        add_column_safe("companies", sa.Column("owner_id", sa.Integer()))
        create_index_safe("ix_companies_owner_id", "companies", ["owner_id"])

    # --- USERS: мягкие расширения ---
    if table_exists("users"):
        add_column_safe("users", sa.Column("username", sa.String(50)))
        add_column_safe("users", sa.Column("tenant_id", sa.Integer()))
        add_column_safe("users", sa.Column("deleted_at", sa.DateTime(timezone=True)))
        add_column_safe("users", sa.Column("last_modified_by", sa.Integer()))
        add_column_safe("users", sa.Column("locked_at", sa.DateTime(timezone=True)))
        add_column_safe("users", sa.Column("locked_by", sa.Integer()))
        # phone -> nullable (при необходимости)
        if column_exists("users", "phone"):
            op.alter_column("users", "phone", existing_type=sa.VARCHAR(length=20), nullable=True)
        create_index_safe("ix_users_username", "users", ["username"], unique=True)
        create_unique_constraint_safe("uq_user_username", "users", ["username"])
        create_index_safe("ix_users_tenant_id", "users", ["tenant_id"])
        create_index_safe("ix_users_last_modified_by", "users", ["last_modified_by"])
        create_index_safe("ix_users_locked_by", "users", ["locked_by"])
        # company_id FK уже есть из 1f107bc8bf2e, оставляем as-is (ondelete=CASCADE)


def downgrade() -> None:
    """Снимаем добавленное этой ревизией (не трогаем объекты из 1f107bc8bf2e)."""

    # STOCK MOVEMENTS
    if table_exists("stock_movements"):
        for idx in [
            "ix_stock_movements_reference_id",
            "ix_stock_movements_movement_type",
            "ix_stock_movements_user_id",
            "ix_stock_movements_stock_id",
        ]:
            drop_index_if_exists(idx, "stock_movements")
        op.drop_table("stock_movements")

    # PAYMENT REFUNDS
    if table_exists("payment_refunds"):
        for idx in [
            "ix_payment_refunds_status",
            "ix_payment_refunds_refund_number",
            "ix_payment_refunds_payment_id",
            "ix_payment_refunds_external_id",
        ]:
            drop_index_if_exists(idx, "payment_refunds")
        op.drop_table("payment_refunds")

    # PRODUCT STOCKS
    if table_exists("product_stocks"):
        for idx in ["ix_product_stocks_warehouse_id", "ix_product_stocks_product_id"]:
            drop_index_if_exists(idx, "product_stocks")
        op.drop_table("product_stocks")

    # PAYMENTS
    if table_exists("payments"):
        for idx in [
            "ix_payments_status",
            "ix_payments_provider_invoice_id",
            "ix_payments_payment_number",
            "ix_payments_order_id",
            "ix_payments_external_id",
        ]:
            drop_index_if_exists(idx, "payments")
        op.drop_table("payments")

    # ORDER ITEMS
    if table_exists("order_items"):
        for idx in ["ix_order_items_product_id", "ix_order_items_order_id"]:
            drop_index_if_exists(idx, "order_items")
        op.drop_table("order_items")

    # WAREHOUSES
    if table_exists("warehouses"):
        for idx in ["ix_warehouses_is_active", "ix_warehouses_code", "ix_warehouses_company_id"]:
            drop_index_if_exists(idx, "warehouses")
        op.drop_table("warehouses")

    # ORDERS
    if table_exists("orders"):
        for idx in [
            "ix_orders_status",
            "ix_orders_source",
            "ix_orders_order_number",
            "ix_orders_external_id",
            "ix_orders_customer_phone",
            "ix_orders_company_id",
        ]:
            drop_index_if_exists(idx, "orders")
        op.drop_table("orders")

    # USERS (только то, что добавили здесь)
    if table_exists("users"):
        for idx in [
            "ix_users_locked_by",
            "ix_users_last_modified_by",
            "ix_users_tenant_id",
            "ix_users_username",
        ]:
            drop_index_if_exists(idx, "users")
        for col in [
            "locked_by",
            "locked_at",
            "last_modified_by",
            "deleted_at",
            "tenant_id",
            "username",
        ]:
            if column_exists("users", col):
                op.drop_column("users", col)

    # COMPANIES
    if table_exists("companies"):
        drop_index_if_exists("ix_companies_owner_id", "companies")
        for col in ["owner_id", "deleted_at"]:
            if column_exists("companies", col):
                op.drop_column("companies", col)

    # AUDIT_LOGS
    if table_exists("audit_logs"):
        for idx in [
            "ix_audit_logs_last_modified_by",
            "ix_audit_logs_tenant_id",
            "ix_audit_logs_warehouse_id",
        ]:
            drop_index_if_exists(idx, "audit_logs")
        for col in ["last_modified_by", "tenant_id", "warehouse_id"]:
            if column_exists("audit_logs", col):
                op.drop_column("audit_logs", col)
