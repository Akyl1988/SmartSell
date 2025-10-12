"""sync models

Revision ID: 48e583d830c1
Revises: b1f1a6b0d3a1
Create Date: 2025-10-08 18:31:31.250488+00:00

"""
from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "48e583d830c1"
down_revision: Union[str, Sequence[str], None] = "b1f1a6b0d3a1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# === Injected helpers: robust ENUM management and logging ===
import logging

logger = logging.getLogger(__name__)
logger = logging.getLogger("alembic.migration")
logger.setLevel(logging.INFO)

from sqlalchemy.dialects import postgresql as psql

# === BEGIN: module-level ENUM objects (fixed) ===
# These reference already-existing DB enum types and DO NOT attempt to create new ones.
orderstatus_enum = psql.ENUM(name="orderstatus", create_type=False)
paymentstatus_enum = psql.ENUM(name="paymentstatus", create_type=False)
paymentprovider_enum = psql.ENUM(name="paymentprovider", create_type=False)
reconciliationstatus_enum = psql.ENUM(name="reconciliationstatus", create_type=False)
campaign_status_enum = psql.ENUM(name="campaign_status", create_type=False)
message_channel_enum = psql.ENUM(name="message_channel", create_type=False)
message_status_enum = psql.ENUM(name="message_status", create_type=False)
paymentmethod_enum = psql.ENUM(name="paymentmethod", create_type=False)
# === END: module-level ENUM objects (fixed) ===

# Unified ENUM labels (supersets to avoid future breaks)
ENUM_LABELS = {
    "orderstatus": (
        "PENDING",
        "CONFIRMED",
        "PAID",
        "PROCESSING",
        "SHIPPED",
        "DELIVERED",
        "COMPLETED",
        "CANCELLED",
        "REFUNDED",
    ),
    "paymentstatus": (
        "PENDING",
        "AUTHORIZED",
        "CAPTURED",
        "PROCESSING",
        "SUCCESS",
        "FAILED",
        "CANCELLED",
        "REFUNDED",
        "PARTIALLY_REFUNDED",
        "CHARGEBACK",
    ),
    "paymentprovider": (
        "TIPTOP",
        "KASPI",
        "PAYBOX",
        "MANUAL",
        "STRIPE",
        "PAYPAL",
        "ADYEN",
        "YOO_KASSA",
        "CLOUDPAYMENTS",
        "FAKE",
    ),
    "reconciliationstatus": ("PENDING", "MATCHED", "MISSING", "MISMATCH", "RESOLVED"),
    "campaign_status": ("DRAFT", "ACTIVE", "PAUSED", "COMPLETED"),
    "message_channel": ("EMAIL", "SMS", "WHATSAPP", "TELEGRAM", "VIBER", "PUSH"),
    "message_status": ("PENDING", "QUEUED", "SENT", "DELIVERED", "FAILED", "OPENED", "CLICKED"),
    "paymentmethod": (
        "CARD",
        "CASH",
        "TRANSFER",
        "CRYPTO",
        "OTHER",
        "BANK_TRANSFER",
        "WALLET",
        "QR_CODE",
    ),
}


def _pg_type_exists(conn, type_name: str) -> bool:
    res = conn.exec_driver_sql(
        "SELECT 1 FROM pg_type WHERE typname = %s",
        (type_name,),
    ).fetchone()
    return bool(res)


def ensure_enum_exists(enum_name: str, values: tuple[str, ...]) -> None:
    conn = op.get_bind()
    if not _pg_type_exists(conn, enum_name):
        logger.info("Creating ENUM %s", enum_name)
        psql.ENUM(*values, name=enum_name, create_type=True).create(conn, checkfirst=True)


def add_enum_values(enum_name: str, values: tuple[str, ...]) -> None:
    conn = op.get_bind()
    # fetch existing labels
    rows = conn.exec_driver_sql(
        """
        SELECT e.enumlabel
FROM pg_type t
        JOIN pg_enum e ON e.enumtypid = t.oid
        WHERE t.typname = %s
        ORDER BY e.enumsortorder
""",
        (enum_name,),
    ).fetchall()
    existing = {r[0] for r in rows}
    for v in values:
        if v not in existing:
            logger.info("Adding value '%s' to ENUM %s", v, enum_name)
            # safe add if not exists
            conn.exec_driver_sql(
                "ALTER TYPE "
                + enum_name
                + " ADD VALUE IF NOT EXISTS '"
                + v.replace("'", "''")
                + "'"
            )


def prepare_all_enums():
    try:
        for name, vals in ENUM_LABELS.items():
            ensure_enum_exists(name, vals)
            add_enum_values(name, vals)
    except Exception as e:
        logger.warning("ENUM preparation step failed: %s", e)
    # === End injected helpers ===

    # === Pre-declared named ENUM type objects (reference existing DB types; no auto-creation) ===
    # Use these in ALL columns and ALTERs to prevent implicit CREATE TYPE during table creation.
    orderstatus_enum = psql.ENUM(name="orderstatus", create_type=False)
    paymentstatus_enum = psql.ENUM(name="paymentstatus", create_type=False)
    paymentprovider_enum = psql.ENUM(name="paymentprovider", create_type=False)
    reconciliationstatus_enum = psql.ENUM(name="reconciliationstatus", create_type=False)
    campaign_status_enum = psql.ENUM(name="campaign_status", create_type=False)
    message_channel_enum = psql.ENUM(name="message_channel", create_type=False)
    message_status_enum = psql.ENUM(name="message_status", create_type=False)
    paymentmethod_enum = psql.ENUM(name="paymentmethod", create_type=False)
    # === End ENUM objects ===

    # === integration_outbox (С‡РёСЃС‚С‹Р№ Рё Р±РµР·РѕРїР°СЃРЅС‹Р№ РІР°СЂРёР°РЅС‚ С‡РµСЂРµР· РѕС‚РґРµР»СЊРЅС‹Рµ РѕРїРµСЂР°С†РёРё) ===
    op.create_table(
        "integration_outbox",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("aggregate_type", sa.String(length=64), nullable=False),
        sa.Column("aggregate_id", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
    )

    # check-constraint Рё PK РґРѕР±Р°РІР»СЏРµРј РѕС‚РґРµР»СЊРЅС‹РјРё РєРѕРјР°РЅРґР°РјРё вЂ” СЌС‚Рѕ СѓСЃС‚СЂР°РЅСЏРµС‚ Р»СЋР±С‹Рµ РїСЂРѕР±Р»РµРјС‹ СЃ Р·Р°РїСЏС‚С‹РјРё/СЃРєРѕР±РєР°РјРё
    op.create_check_constraint(
        op.f("ck__integration_outbox__ck_outbox_attempts_nonneg"),
        "integration_outbox",
        "attempts >= 0",
    )

    op.create_primary_key(
        op.f("pk__integration_outbox"),
        "integration_outbox",
        ["id"],
    )

    # РёРЅРґРµРєСЃС‹
    op.create_index(
        op.f("ix_integration_outbox_aggregate_id"),
        "integration_outbox",
        ["aggregate_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_integration_outbox_aggregate_type"),
        "integration_outbox",
        ["aggregate_type"],
        unique=False,
    )
    op.create_index(
        op.f("ix_integration_outbox_created_at"), "integration_outbox", ["created_at"], unique=False
    )
    op.create_index(
        op.f("ix_integration_outbox_event_type"), "integration_outbox", ["event_type"], unique=False
    )
    op.create_index(op.f("ix_integration_outbox_id"), "integration_outbox", ["id"], unique=False)
    op.create_index(
        op.f("ix_integration_outbox_status"), "integration_outbox", ["status"], unique=False
    )
    op.create_index(
        "ix_outbox_status_due", "integration_outbox", ["status", "next_attempt_at"], unique=False
    )
    # === /integration_outbox ===

    # === otp_attempts (СЃРѕР·РґР°С‘Рј С‚Р°Р±Р»РёС†Сѓ, Р° РІСЃС‘ РѕСЃС‚Р°Р»СЊРЅРѕРµ вЂ” РѕС‚РґРµР»СЊРЅС‹РјРё РѕРїРµСЂР°С†РёСЏРјРё) ===
    op.create_table(
        "otp_attempts",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("phone", sa.String(length=32), nullable=False),
        sa.Column("code_hash", sa.String(length=255), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("attempts_left", sa.Integer(), nullable=False),
        sa.Column("is_verified", sa.Boolean(), nullable=False),
        sa.Column("sent_count_hour", sa.Integer(), nullable=False),
        sa.Column("sent_count_day", sa.Integer(), nullable=False),
        sa.Column("last_sent_at", sa.DateTime(), nullable=True),
        sa.Column("hour_window_started_at", sa.DateTime(), nullable=True),
        sa.Column("day_window_started_at", sa.DateTime(), nullable=True),
        sa.Column("purpose", sa.String(length=32), nullable=False),
        sa.Column("channel", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.Column("delete_reason", sa.String(length=64), nullable=True),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("is_blocked", sa.Boolean(), nullable=False),
        sa.Column("blocked_until", sa.DateTime(), nullable=True),
        sa.Column("block_reason", sa.String(length=64), nullable=True),
        sa.Column("fraud_score", sa.Integer(), nullable=False),
        sa.Column("fraud_flags", sa.String(length=255), nullable=True),
    )

    # PK
    op.create_primary_key(op.f("pk__otp_attempts"), "otp_attempts", ["id"])

    # (РћРїС†РёРѕРЅР°Р»СЊРЅРѕ) РґСѓР±Р»РёСЂСѓСЋС‰РёР№ UK РЅР° id вЂ” РїРѕРІС‚РѕСЂСЏРµС‚ PK, РЅРѕ РѕСЃС‚Р°РІР»СЏСЋ РґР»СЏ СЃРѕРІРјРµСЃС‚РёРјРѕСЃС‚Рё СЃРѕ СЃС‚Р°СЂРѕР№ СЃС…РµРјРѕР№
    op.create_unique_constraint("uq_otp_attempts_id", "otp_attempts", ["id"])

    # FK РЅР° users(id)
    op.create_foreign_key(
        op.f("fk__otp_attempts__user_id__users"),
        "otp_attempts",
        "users",
        ["user_id"],
        ["id"],
        ondelete="SET NULL",
    )

    # CHECK-РѕРіСЂР°РЅРёС‡РµРЅРёСЏ
    op.create_check_constraint(
        op.f("ck__otp_attempts__ck_otp_attempts_left_non_negative"),
        "otp_attempts",
        "attempts_left >= 0",
    )
    op.create_check_constraint(
        op.f("ck__otp_attempts__ck_otp_fraud_score_non_negative"),
        "otp_attempts",
        "fraud_score >= 0",
    )
    op.create_check_constraint(
        op.f("ck__otp_attempts__ck_otp_sent_counts_non_negative"),
        "otp_attempts",
        "sent_count_hour >= 0 AND sent_count_day >= 0",
    )

    # РРЅРґРµРєСЃС‹ (РєР°Рє Р±С‹Р»Рё РІ СЃС‚Р°СЂРѕРј С„Р°Р№Р»Рµ)
    op.create_index("ix_otp_attempt_blocked", "otp_attempts", ["is_blocked"], unique=False)
    op.create_index("ix_otp_attempt_channel", "otp_attempts", ["channel"], unique=False)
    op.create_index("ix_otp_attempt_created_at", "otp_attempts", ["created_at"], unique=False)
    op.create_index("ix_otp_attempt_expires_at", "otp_attempts", ["expires_at"], unique=False)
    op.create_index("ix_otp_attempt_phone", "otp_attempts", ["phone"], unique=False)
    op.create_index("ix_otp_attempt_purpose", "otp_attempts", ["purpose"], unique=False)
    op.create_index("ix_otp_attempt_verified", "otp_attempts", ["is_verified"], unique=False)
    op.create_index(op.f("ix_otp_attempts_id"), "otp_attempts", ["id"], unique=False)
    # === /otp_attempts ===

    # === billing_invoices (fixed) ===
    op.create_table(
        "billing_invoices",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("order_id", sa.Integer(), nullable=True),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("number", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("currency", sa.String(length=8), nullable=False),
        sa.Column("subtotal", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("tax_amount", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("discount_amount", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("total_amount", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("total_due", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("paid_amount", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("meta", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint(
            "paid_amount >= 0", name=op.f("ck__billing_invoices__ck_bi_paid_amount_nonneg")
        ),
        sa.CheckConstraint(
            "subtotal >= 0 AND tax_amount >= 0 AND discount_amount >= 0 AND total_amount >= 0",
            name=op.f("ck__billing_invoices__ck_bi_non_negative"),
        ),
        sa.CheckConstraint(
            "total_due >= 0", name=op.f("ck__billing_invoices__ck_bi_total_due_nonneg")
        ),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            name=op.f("fk__billing_invoices__company_id__companies"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["order_id"],
            ["orders.id"],
            name=op.f("fk__billing_invoices__order_id__orders"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk__billing_invoices")),
    )

    # РРЅРґРµРєСЃС‹ (РІРЅРµ create_table)
    op.create_index(
        op.f("ix_billing_invoices_company_id"), "billing_invoices", ["company_id"], unique=False
    )
    op.create_index(
        "ix_billing_invoices_company_status",
        "billing_invoices",
        ["company_id", "status"],
        unique=False,
    )
    op.create_index(
        op.f("ix_billing_invoices_created_at"), "billing_invoices", ["created_at"], unique=False
    )
    op.create_index(
        op.f("ix_billing_invoices_deleted_at"), "billing_invoices", ["deleted_at"], unique=False
    )
    op.create_index(
        op.f("ix_billing_invoices_due_at"), "billing_invoices", ["due_at"], unique=False
    )
    op.create_index(op.f("ix_billing_invoices_id"), "billing_invoices", ["id"], unique=False)
    op.create_index(
        op.f("ix_billing_invoices_issued_at"), "billing_invoices", ["issued_at"], unique=False
    )
    op.create_index(op.f("ix_billing_invoices_number"), "billing_invoices", ["number"], unique=True)
    op.create_index(
        op.f("ix_billing_invoices_order_id"), "billing_invoices", ["order_id"], unique=True
    )
    op.create_index(
        op.f("ix_billing_invoices_paid_at"), "billing_invoices", ["paid_at"], unique=False
    )
    op.create_index(
        op.f("ix_billing_invoices_status"), "billing_invoices", ["status"], unique=False
    )
    op.create_index(
        "ix_billing_invoices_status_due", "billing_invoices", ["status", "due_at"], unique=False
    )
    op.create_index(
        op.f("ix_billing_invoices_updated_at"), "billing_invoices", ["updated_at"], unique=False
    )
    # === /billing_invoices ===

    op.create_table(
        "order_status_history",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("order_id", sa.Integer(), nullable=False),
        sa.Column("old_status", orderstatus_enum, nullable=False),
        sa.Column("new_status", orderstatus_enum, nullable=False),
        sa.Column("changed_by", sa.Integer(), nullable=True),
        sa.Column("changed_at", sa.DateTime(), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["order_id"],
            ["orders.id"],
            name=op.f("fk__order_status_history__order_id__orders"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "id",
            name=op.f("pk__order_status_history"),
        ),
    )
    op.create_index(
        op.f("ix_order_status_history_changed_at"),
        "order_status_history",
        ["changed_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_order_status_history_changed_by"),
        "order_status_history",
        ["changed_by"],
        unique=False,
    )
    op.create_index(
        op.f("ix_order_status_history_order_id"), "order_status_history", ["order_id"], unique=False
    )
    op.create_table(
        "provider_reconciliation",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("provider", paymentprovider_enum, nullable=False),
        sa.Column("statement_at", sa.DateTime(), nullable=False),
        sa.Column("external_id", sa.String(length=128), nullable=False),
        sa.Column("amount", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("currency", sa.String(length=8), nullable=False),
        sa.Column("matched_payment_id", sa.Integer(), nullable=True),
        sa.Column("status", reconciliationstatus_enum, nullable=False),
        sa.Column("details", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.CheckConstraint(
            "length(currency) >= 3", name=op.f("ck__provider_reconciliation__ck_recon_currency_len")
        ),
        sa.ForeignKeyConstraint(
            ["matched_payment_id"],
            ["payments.id"],
            name=op.f("fk__provider_reconciliation__matched_payment_id__payments"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint(
            "id",
            name=op.f("pk__provider_reconciliation"),
        ),
    )
    op.create_index(
        op.f("ix_provider_reconciliation_created_at"),
        "provider_reconciliation",
        ["created_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_provider_reconciliation_external_id"),
        "provider_reconciliation",
        ["external_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_provider_reconciliation_id"), "provider_reconciliation", ["id"], unique=False
    )
    op.create_index(
        op.f("ix_provider_reconciliation_matched_payment_id"),
        "provider_reconciliation",
        ["matched_payment_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_provider_reconciliation_provider"),
        "provider_reconciliation",
        ["provider"],
        unique=False,
    )
    op.create_index(
        op.f("ix_provider_reconciliation_statement_at"),
        "provider_reconciliation",
        ["statement_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_provider_reconciliation_status"),
        "provider_reconciliation",
        ["status"],
        unique=False,
    )
    op.create_index(
        "ix_recon_provider_external",
        "provider_reconciliation",
        ["provider", "external_id"],
        unique=False,
    )
    op.drop_index(op.f("ix_bot_sessions_company_id"), table_name="bot_sessions")
    op.drop_index(op.f("ix_bot_sessions_id"), table_name="bot_sessions")
    op.drop_index(op.f("ix_bot_sessions_intent"), table_name="bot_sessions")
    op.drop_index(op.f("ix_bot_sessions_last_activity_at"), table_name="bot_sessions")
    op.drop_index(op.f("ix_bot_sessions_session_type"), table_name="bot_sessions")
    op.drop_index(op.f("ix_bot_sessions_status"), table_name="bot_sessions")
    op.drop_index(op.f("ix_bot_sessions_user_id"), table_name="bot_sessions")
    op.drop_table("bot_sessions")
    op.drop_index(op.f("ix_campaign_messages_campaign_id"), table_name="campaign_messages")
    op.drop_index(op.f("ix_messages_channel"), table_name="campaign_messages")
    op.drop_table("campaign_messages")
    op.alter_column(
        "audit_logs",
        "correlation_id",
        existing_type=sa.TEXT(),
        type_=sa.String(length=64),
        existing_nullable=True,
    )
    op.alter_column(
        "audit_logs",
        "source",
        existing_type=sa.TEXT(),
        type_=sa.String(length=64),
        existing_nullable=True,
    )
    op.alter_column(
        "audit_logs",
        "created_at",
        existing_type=postgresql.TIMESTAMP(),
        server_default=None,
        existing_nullable=False,
    )
    op.alter_column(
        "audit_logs",
        "updated_at",
        existing_type=postgresql.TIMESTAMP(),
        server_default=None,
        existing_nullable=False,
    )
    op.drop_index(op.f("idx_audit_logs_entity"), table_name="audit_logs")
    op.drop_index(op.f("idx_audit_logs_product_id"), table_name="audit_logs")
    op.drop_index(op.f("idx_audit_logs_request"), table_name="audit_logs")
    op.drop_index(op.f("ix_audit_logs_entity"), table_name="audit_logs")
    op.create_index("ix_audit_action_user", "audit_logs", ["action", "user_id"], unique=False)
    op.create_index("ix_audit_created_at", "audit_logs", ["created_at"], unique=False)
    op.create_index(
        "ix_audit_entity_type_id", "audit_logs", ["entity_type", "entity_id"], unique=False
    )
    op.create_index(op.f("ix_audit_logs_company_id"), "audit_logs", ["company_id"], unique=False)
    op.create_index(
        op.f("ix_audit_logs_correlation_id"), "audit_logs", ["correlation_id"], unique=False
    )
    op.create_index(op.f("ix_audit_logs_created_at"), "audit_logs", ["created_at"], unique=False)
    op.create_index(op.f("ix_audit_logs_entity_id"), "audit_logs", ["entity_id"], unique=False)
    op.create_index(op.f("ix_audit_logs_entity_type"), "audit_logs", ["entity_type"], unique=False)
    op.create_index(op.f("ix_audit_logs_id"), "audit_logs", ["id"], unique=False)
    op.create_index(op.f("ix_audit_logs_order_id"), "audit_logs", ["order_id"], unique=False)
    op.create_index(op.f("ix_audit_logs_payment_id"), "audit_logs", ["payment_id"], unique=False)
    op.create_index(op.f("ix_audit_logs_product_id"), "audit_logs", ["product_id"], unique=False)
    op.create_index(op.f("ix_audit_logs_request_id"), "audit_logs", ["request_id"], unique=False)
    op.create_index(op.f("ix_audit_logs_source"), "audit_logs", ["source"], unique=False)
    op.create_index(op.f("ix_audit_logs_updated_at"), "audit_logs", ["updated_at"], unique=False)
    op.create_index(op.f("ix_audit_logs_user_id"), "audit_logs", ["user_id"], unique=False)
    op.create_index(
        "ix_audit_wh_created", "audit_logs", ["warehouse_id", "created_at"], unique=False
    )
    op.drop_constraint(op.f("fk_audit_logs_company_id_companies"), "audit_logs", type_="foreignkey")
    op.drop_constraint(op.f("audit_logs_product_id_fkey"), "audit_logs", type_="foreignkey")
    op.create_foreign_key(
        op.f("fk__audit_logs__product_id__products"),
        "audit_logs",
        "products",
        ["product_id"],
        ["id"],
        ondelete="CASCADE",
    )
    (op.add_column("billing_payments", sa.Column("order_id", sa.Integer(), nullable=True)),)
    (op.add_column("billing_payments", sa.Column("method", sa.String(length=32), nullable=False)),)
    (
        op.add_column(
            "billing_payments",
            sa.Column("provider_payment_id", sa.String(length=128), nullable=True),
        ),
    )
    (
        op.add_column(
            "billing_payments",
            sa.Column("provider_receipt_url", sa.String(length=1024), nullable=True),
        ),
    )
    (
        op.add_column(
            "billing_payments",
            sa.Column("authorized_at", sa.DateTime(timezone=True), nullable=True),
        ),
    )
    (
        op.add_column(
            "billing_payments", sa.Column("captured_at", sa.DateTime(timezone=True), nullable=True)
        ),
    )
    (
        op.add_column(
            "billing_payments", sa.Column("refunded_at", sa.DateTime(timezone=True), nullable=True)
        ),
    )
    (
        op.add_column(
            "billing_payments", sa.Column("failed_at", sa.DateTime(timezone=True), nullable=True)
        ),
    )
    (op.add_column("billing_payments", sa.Column("meta", sa.Text(), nullable=True)),)
    (op.add_column("billing_payments", sa.Column("deleted_at", sa.DateTime(), nullable=True)),)
    op.alter_column(
        "billing_payments",
        "amount",
        existing_type=sa.NUMERIC(precision=10, scale=2),
        type_=sa.Numeric(precision=14, scale=2),
        existing_nullable=False,
    )
    op.alter_column(
        "billing_payments",
        "currency",
        existing_type=sa.VARCHAR(length=8),
        server_default=None,
        existing_nullable=False,
    )
    op.alter_column(
        "billing_payments",
        "provider",
        existing_type=sa.VARCHAR(length=32),
        server_default=None,
        type_=sa.String(length=64),
        nullable=True,
    )
    op.alter_column(
        "billing_payments",
        "created_at",
        existing_type=postgresql.TIMESTAMP(timezone=True),
        server_default=None,
        existing_nullable=False,
    )
    op.alter_column(
        "billing_payments",
        "updated_at",
        existing_type=postgresql.TIMESTAMP(timezone=True),
        server_default=None,
        existing_nullable=False,
    )
    op.alter_column(
        "billing_payments",
        "description",
        existing_type=sa.VARCHAR(length=255),
        type_=sa.Text(),
        existing_nullable=True,
    )
    op.drop_constraint(
        op.f("billing_payments_provider_invoice_id_key"), "billing_payments", type_="unique"
    )
    op.drop_index(op.f("ix_billing_payments_payment_type"), table_name="billing_payments")
    op.drop_index(op.f("ix_billing_payments_provider_invoice_id"), table_name="billing_payments")
    op.create_index(
        op.f("ix_billing_payments_authorized_at"),
        "billing_payments",
        ["authorized_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_billing_payments_captured_at"), "billing_payments", ["captured_at"], unique=False
    )
    op.create_index(
        "ix_billing_payments_company_created",
        "billing_payments",
        ["company_id", "created_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_billing_payments_created_at"), "billing_payments", ["created_at"], unique=False
    )
    op.create_index(
        op.f("ix_billing_payments_deleted_at"), "billing_payments", ["deleted_at"], unique=False
    )
    op.create_index(
        op.f("ix_billing_payments_failed_at"), "billing_payments", ["failed_at"], unique=False
    )
    op.create_index(
        op.f("ix_billing_payments_method"), "billing_payments", ["method"], unique=False
    )
    op.create_index(
        "ix_billing_payments_method_created",
        "billing_payments",
        ["method", "created_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_billing_payments_order_id"), "billing_payments", ["order_id"], unique=False
    )
    op.create_index(
        op.f("ix_billing_payments_provider"), "billing_payments", ["provider"], unique=False
    )
    op.create_index(
        op.f("ix_billing_payments_provider_payment_id"),
        "billing_payments",
        ["provider_payment_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_billing_payments_refunded_at"), "billing_payments", ["refunded_at"], unique=False
    )
    op.create_index(
        "ix_billing_payments_status_created",
        "billing_payments",
        ["status", "created_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_billing_payments_updated_at"), "billing_payments", ["updated_at"], unique=False
    )
    op.create_unique_constraint(
        "uq_bp_provider_payment", "billing_payments", ["provider", "provider_payment_id"]
    )
    op.create_foreign_key(
        op.f("fk__billing_payments__order_id__orders"),
        "billing_payments",
        "orders",
        ["order_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.drop_column("billing_payments", "billing_period_end")
    op.drop_column("billing_payments", "processed_at")
    op.drop_column("billing_payments", "billing_period_start")
    op.drop_column("billing_payments", "receipt_number")
    op.drop_column("billing_payments", "provider_invoice_id")
    op.drop_column("billing_payments", "provider_transaction_id")
    op.drop_column("billing_payments", "receipt_url")
    op.drop_column("billing_payments", "payment_type")
    (op.add_column("campaigns", sa.Column("company_id", sa.Integer(), nullable=False)),)
    (op.add_column("campaigns", sa.Column("total_messages", sa.Integer(), nullable=False)),)
    (op.add_column("campaigns", sa.Column("sent_count", sa.Integer(), nullable=False)),)
    (op.add_column("campaigns", sa.Column("delivered_count", sa.Integer(), nullable=False)),)
    (op.add_column("campaigns", sa.Column("failed_count", sa.Integer(), nullable=False)),)
    (
        op.add_column(
            "campaigns", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True)
        ),
    )
    (op.add_column("campaigns", sa.Column("deleted_by", sa.Integer(), nullable=True)),)
    (op.add_column("campaigns", sa.Column("delete_reason", sa.Text(), nullable=True)),)
    # -- Safe cast campaigns.status -> campaign_status with explicit USING (idempotent)
    # --- Safe, idempotent cast of campaigns.status -> campaign_status ---
    # 1) СЃРЅРёРјР°РµРј DEFAULT, С‡С‚РѕР±С‹ Postgres РЅРµ РїС‹С‚Р°Р»СЃСЏ РїСЂРёРІРµСЃС‚Рё СЃС‚Р°СЂРѕРµ РІС‹СЂР°Р¶РµРЅРёРµ Рє РЅРѕРІРѕРјСѓ enum
    op.execute("ALTER TABLE campaigns ALTER COLUMN status DROP DEFAULT;")

    # 2) РЅРѕСЂРјР°Р»РёР·СѓРµРј РґР°РЅРЅС‹Рµ (РµСЃР»Рё РІ С‚Р°Р±Р»РёС†Рµ РІРґСЂСѓРі РµСЃС‚СЊ РЅРµРѕР¶РёРґР°РЅРЅС‹Рµ СЃС‚СЂРѕРєРё/NULL)
    op.execute(
        """
    UPDATE campaigns
    SET status = 'DRAFT'
    WHERE status IS NULL
       OR status::text NOT IN ('DRAFT','ACTIVE','PAUSED','COMPLETED');
    """
    )

    # 3) РјРµРЅСЏРµРј С‚РёРї РєРѕР»РѕРЅРєРё РЅР° enum СЃ СЏРІРЅС‹Рј USING (РїСЂР°РІРёР»СЊРЅС‹Р№ DO-Р±Р»РѕРє Р±РµР· Р»РёС€РЅРµРіРѕ `$`)
    op.execute(
        """
    DO $$
    DECLARE
        cur_type text;
    BEGIN
        SELECT t.typname INTO cur_type
        FROM pg_attribute a
        JOIN pg_class c ON c.oid = a.attrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        JOIN pg_type t ON t.oid = a.atttypid
        WHERE c.relname = 'campaigns'
          AND n.nspname = current_schema()
          AND a.attname = 'status'
          AND NOT a.attisdropped;

        IF cur_type IS NOT NULL AND cur_type <> 'campaign_status' THEN
            EXECUTE 'ALTER TABLE campaigns ALTER COLUMN status TYPE campaign_status USING status::text::campaign_status';
        END IF;
    END;
    $$;
    """
    )

    # 4) РІРѕР·РІСЂР°С‰Р°РµРј РєРѕСЂСЂРµРєС‚РЅС‹Р№ DEFAULT РґР»СЏ РЅРѕРІРѕРіРѕ enum
    op.execute("ALTER TABLE campaigns ALTER COLUMN status SET DEFAULT 'DRAFT'::campaign_status")

    # РџСЂРёРІРѕРґРёРј campaigns.scheduled_at -> timestamptz, СЃС‡РёС‚Р°СЏ СЃС‚Р°СЂС‹Рµ Р·РЅР°С‡РµРЅРёСЏ РІСЂРµРјРµРЅРµРј РђР»РјР°С‚С‹
    op.alter_column(
        "campaigns",
        "scheduled_at",
        type_=sa.DateTime(timezone=True),
        existing_nullable=True,
        postgresql_using="(scheduled_at AT TIME ZONE 'Asia/Almaty')",
    )

    op.drop_index(op.f("ix_campaigns_active_archived"), table_name="campaigns")
    op.drop_index(op.f("ix_campaigns_status"), table_name="campaigns")
    op.drop_index(op.f("ix_campaigns_title_lower"), table_name="campaigns")
    op.drop_constraint(op.f("uq_campaign_title"), "campaigns", type_="unique")
    op.create_index(
        "ix_campaign_company_status", "campaigns", ["company_id", "status"], unique=False
    )
    op.create_index(
        "ix_campaign_scheduled_at_status", "campaigns", ["scheduled_at", "status"], unique=False
    )
    op.create_index(op.f("ix_campaigns_company_id"), "campaigns", ["company_id"], unique=False)
    op.create_index(op.f("ix_campaigns_deleted_at"), "campaigns", ["deleted_at"], unique=False)
    op.create_index(op.f("ix_campaigns_deleted_by"), "campaigns", ["deleted_by"], unique=False)
    op.create_index(op.f("ix_campaigns_scheduled_at"), "campaigns", ["scheduled_at"], unique=False)
    op.create_unique_constraint(
        "uq_campaign_company_title_scheduled", "campaigns", ["company_id", "title", "scheduled_at"]
    )
    op.create_foreign_key(
        op.f("fk__campaigns__company_id__companies"),
        "campaigns",
        "companies",
        ["company_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.drop_column("campaigns", "active")
    op.drop_column("campaigns", "updated_at")
    op.drop_column("campaigns", "schedule")
    op.drop_column("campaigns", "tags")
    op.drop_column("campaigns", "owner")
    op.drop_column("campaigns", "archived")
    op.drop_column("campaigns", "created_at")
    (op.add_column("categories", sa.Column("deleted_at", sa.DateTime(), nullable=True)),)
    op.alter_column(
        "categories",
        "is_active",
        existing_type=sa.BOOLEAN(),
        server_default=sa.text("true"),
        existing_nullable=False,
    )
    op.alter_column(
        "categories",
        "sort_order",
        existing_type=sa.INTEGER(),
        server_default=sa.text("0"),
        existing_nullable=False,
    )
    op.alter_column(
        "categories",
        "created_at",
        existing_type=postgresql.TIMESTAMP(),
        server_default=sa.text("now()"),
        existing_nullable=False,
    )
    op.drop_constraint(op.f("uq_category_slug"), "categories", type_="unique")
    op.create_index(op.f("ix_categories_deleted_at"), "categories", ["deleted_at"], unique=False)
    op.create_foreign_key(
        op.f("fk__categories__parent_id__categories"),
        "categories",
        "categories",
        ["parent_id"],
        ["id"],
        ondelete="SET NULL",
    )
    (op.add_column("companies", sa.Column("external_id", sa.String(length=64), nullable=True)),)
    (op.add_column("companies", sa.Column("onec_id", sa.String(length=64), nullable=True)),)
    (op.add_column("companies", sa.Column("sync_source", sa.String(length=32), nullable=True)),)
    (op.add_column("companies", sa.Column("synced_at", sa.DateTime(timezone=True), nullable=True)),)
    (
        op.add_column(
            "companies", sa.Column("last_sync_error_code", sa.String(length=64), nullable=True)
        ),
    )
    (op.add_column("companies", sa.Column("last_sync_error_message", sa.Text(), nullable=True)),)
    (
        op.add_column(
            "companies",
            sa.Column("settings_version", sa.Integer(), nullable=True, server_default=sa.text("0")),
        ),
    )
    op.execute("UPDATE companies SET settings_version = 0 WHERE settings_version IS NULL")
    op.alter_column("companies", "settings_version", nullable=False)
    op.alter_column("companies", "settings_version", server_default=None)
    (op.add_column("companies", sa.Column("settings_history", sa.Text(), nullable=True)),)
    (op.add_column("companies", sa.Column("deleted_by", sa.Integer(), nullable=True)),)
    (op.add_column("companies", sa.Column("delete_reason", sa.Text(), nullable=True)),)
    (
        op.add_column(
            "companies", sa.Column("gdpr_consent_at", sa.DateTime(timezone=True), nullable=True)
        ),
    )
    (
        op.add_column(
            "companies", sa.Column("gdpr_consent_version", sa.String(length=32), nullable=True)
        ),
    )
    (op.add_column("companies", sa.Column("gdpr_consent_ip", sa.String(length=45), nullable=True)),)
    op.alter_column(
        "companies",
        "is_active",
        existing_type=sa.Boolean(),
        server_default=None,
        existing_nullable=False,
    )

    op.alter_column(
        "companies",
        "subscription_plan",
        existing_type=sa.String(length=32),
        server_default=None,
        existing_nullable=False,
    )

    op.alter_column(
        "companies",
        "subscription_expires_at",
        type_=sa.DateTime(timezone=True),
        existing_type=postgresql.TIMESTAMP(),  # СЂР°РЅРµРµ Р±С‹Р» TIMESTAMP Р±РµР· tz
        existing_nullable=True,
        postgresql_using="(subscription_expires_at::timestamp AT TIME ZONE 'Asia/Almaty')",
    )

    op.alter_column(
        "companies",
        "created_at",
        existing_type=postgresql.TIMESTAMP(),
        server_default=None,
        type_=sa.DateTime(timezone=True),
        existing_nullable=False,
    )

    op.alter_column(
        "companies",
        "updated_at",
        existing_type=postgresql.TIMESTAMP(),
        server_default=None,
        type_=sa.DateTime(timezone=True),
        existing_nullable=False,
    )

    # companies.created_at -> timestamptz
    op.alter_column(
        "companies",
        "created_at",
        type_=sa.DateTime(timezone=True),
        existing_type=postgresql.TIMESTAMP(timezone=False),
        server_default=None,
        existing_nullable=False,
    )

    # companies.updated_at -> timestamptz
    op.alter_column(
        "companies",
        "updated_at",
        type_=sa.DateTime(timezone=True),
        existing_type=postgresql.TIMESTAMP(timezone=False),
        server_default=None,
        existing_nullable=False,
    )

    op.create_index(op.f("ix_companies_created_at"), "companies", ["created_at"], unique=False)
    op.create_index(op.f("ix_companies_deleted_at"), "companies", ["deleted_at"], unique=False)
    op.create_index(op.f("ix_companies_deleted_by"), "companies", ["deleted_by"], unique=False)
    op.create_index(op.f("ix_companies_external_id"), "companies", ["external_id"], unique=True)
    op.create_index(
        op.f("ix_companies_gdpr_consent_at"), "companies", ["gdpr_consent_at"], unique=False
    )
    op.create_index(
        op.f("ix_companies_gdpr_consent_version"),
        "companies",
        ["gdpr_consent_version"],
        unique=False,
    )
    op.create_index(op.f("ix_companies_id"), "companies", ["id"], unique=False)
    op.create_index(
        op.f("ix_companies_last_sync_error_code"),
        "companies",
        ["last_sync_error_code"],
        unique=False,
    )
    op.create_index(op.f("ix_companies_onec_id"), "companies", ["onec_id"], unique=True)
    op.create_index(op.f("ix_companies_sync_source"), "companies", ["sync_source"], unique=False)
    op.create_index(op.f("ix_companies_synced_at"), "companies", ["synced_at"], unique=False)
    op.create_index(op.f("ix_companies_updated_at"), "companies", ["updated_at"], unique=False)
    op.create_index(
        "ix_company_active_plan", "companies", ["is_active", "subscription_plan"], unique=False
    )
    op.create_foreign_key(
        op.f("fk__companies__owner_id__users"),
        "companies",
        "users",
        ["owner_id"],
        ["id"],
        ondelete="SET NULL",
    )
    (op.add_column("customers", sa.Column("tenant_id", sa.Integer(), nullable=True)),)
    (op.add_column("customers", sa.Column("deleted_at", sa.DateTime(), nullable=True)),)
    (op.add_column("customers", sa.Column("last_modified_by", sa.Integer(), nullable=True)),)
    op.alter_column(
        "customers",
        "is_active",
        existing_type=sa.BOOLEAN(),
        server_default=None,
        existing_nullable=False,
    )
    op.alter_column(
        "customers",
        "updated_at",
        existing_type=postgresql.TIMESTAMP(),
        server_default=None,
        existing_nullable=False,
    )
    op.create_index("ix_customers_active_email", "customers", ["is_active", "email"], unique=False)
    op.create_index(op.f("ix_customers_deleted_at"), "customers", ["deleted_at"], unique=False)
    op.create_index(op.f("ix_customers_id"), "customers", ["id"], unique=False)
    op.create_index(
        op.f("ix_customers_last_modified_by"), "customers", ["last_modified_by"], unique=False
    )
    op.create_index("ix_customers_phone", "customers", ["phone"], unique=False)
    op.create_index(op.f("ix_customers_tenant_id"), "customers", ["tenant_id"], unique=False)
    # РћС‡РёСЃС‚РєР° РїРµСЂРµРґ РєР°СЃС‚РѕРј РІ JSONB
    op.execute(
        """
    UPDATE inventory_outbox
       SET payload = '{}'
     WHERE payload IS NULL
        OR NOT (payload IS JSON)
    """
    )

    op.alter_column(
        "inventory_outbox",
        "payload",
        existing_type=sa.TEXT(),  # РёР»Рё postgresql.JSON(), РµСЃР»Рё СЂР°РЅСЊС€Рµ Р±С‹Р» JSON
        type_=postgresql.JSONB(astext_type=sa.Text()),
        postgresql_using="payload::jsonb",
        existing_nullable=True,  # СѓРєР°Р¶РёС‚Рµ С‚РµРєСѓС‰СѓСЋ NULL-РЅРѕСЃС‚СЊ, РµСЃР»Рё Alembic РµС‘ Р·РЅР°РµС‚
        nullable=True,  # Р¶РµР»Р°РµРјР°СЏ NULL-РЅРѕСЃС‚СЊ
    )
    op.alter_column(
        "inventory_outbox",
        "channel",
        existing_type=sa.VARCHAR(length=64),
        server_default=None,
        nullable=True,
    )
    op.alter_column(
        "inventory_outbox",
        "status",
        existing_type=sa.VARCHAR(length=32),
        server_default=None,
        type_=sa.String(length=16),
        existing_nullable=False,
    )
    op.alter_column(
        "inventory_outbox",
        "attempts",
        existing_type=sa.INTEGER(),
        server_default=None,
        existing_nullable=False,
    )
    op.alter_column(
        "inventory_outbox",
        "created_at",
        existing_type=postgresql.TIMESTAMP(),
        server_default=None,
        existing_nullable=False,
    )
    op.alter_column(
        "inventory_outbox",
        "updated_at",
        existing_type=postgresql.TIMESTAMP(),
        server_default=None,
        existing_nullable=False,
    )
    op.drop_index(op.f("ix_outbox_aggregate"), table_name="inventory_outbox")
    op.drop_index(op.f("ix_outbox_event"), table_name="inventory_outbox")
    op.drop_index(op.f("ix_outbox_status"), table_name="inventory_outbox")
    op.create_index(
        "ix_inv_outbox_aggregate_created",
        "inventory_outbox",
        ["aggregate_type", "aggregate_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_inv_outbox_status_due", "inventory_outbox", ["status", "next_attempt_at"], unique=False
    )
    op.create_index(
        op.f("ix_inventory_outbox_aggregate_id"), "inventory_outbox", ["aggregate_id"], unique=False
    )
    op.create_index(
        op.f("ix_inventory_outbox_aggregate_type"),
        "inventory_outbox",
        ["aggregate_type"],
        unique=False,
    )
    op.create_index(
        op.f("ix_inventory_outbox_channel"), "inventory_outbox", ["channel"], unique=False
    )
    op.create_index(
        op.f("ix_inventory_outbox_created_at"), "inventory_outbox", ["created_at"], unique=False
    )
    op.create_index(
        op.f("ix_inventory_outbox_deleted_at"), "inventory_outbox", ["deleted_at"], unique=False
    )
    op.create_index(
        op.f("ix_inventory_outbox_event_type"), "inventory_outbox", ["event_type"], unique=False
    )
    op.create_index(op.f("ix_inventory_outbox_id"), "inventory_outbox", ["id"], unique=False)
    op.create_index(
        op.f("ix_inventory_outbox_next_attempt_at"),
        "inventory_outbox",
        ["next_attempt_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_inventory_outbox_processed_at"), "inventory_outbox", ["processed_at"], unique=False
    )
    op.create_index(
        op.f("ix_inventory_outbox_status"), "inventory_outbox", ["status"], unique=False
    )
    op.create_index(
        op.f("ix_inventory_outbox_updated_at"), "inventory_outbox", ["updated_at"], unique=False
    )
    (op.add_column("invoices", sa.Column("deleted_at", sa.DateTime(), nullable=True)),)
    op.alter_column(
        "invoices",
        "subtotal",
        existing_type=sa.NUMERIC(precision=10, scale=2),
        type_=sa.Numeric(precision=14, scale=2),
        existing_nullable=False,
    )
    op.alter_column(
        "invoices",
        "tax_amount",
        existing_type=sa.NUMERIC(precision=10, scale=2),
        server_default=None,
        type_=sa.Numeric(precision=14, scale=2),
        nullable=False,
    )
    op.alter_column(
        "invoices",
        "total_amount",
        existing_type=sa.NUMERIC(precision=10, scale=2),
        type_=sa.Numeric(precision=14, scale=2),
        existing_nullable=False,
    )
    op.alter_column(
        "invoices",
        "currency",
        existing_type=sa.VARCHAR(length=8),
        server_default=None,
        existing_nullable=False,
    )
    op.alter_column(
        "invoices",
        "created_at",
        existing_type=postgresql.TIMESTAMP(timezone=True),
        server_default=None,
        type_=sa.DateTime(),
        existing_nullable=False,
    )
    op.alter_column(
        "invoices",
        "updated_at",
        existing_type=postgresql.TIMESTAMP(timezone=True),
        server_default=None,
        type_=sa.DateTime(),
        existing_nullable=False,
    )
    op.drop_constraint(op.f("invoices_invoice_number_key"), "invoices", type_="unique")
    op.drop_index(op.f("ix_invoices_invoice_number"), table_name="invoices")
    op.create_index(op.f("ix_invoices_invoice_number"), "invoices", ["invoice_number"], unique=True)
    op.create_index(
        "ix_invoice_company_created", "invoices", ["company_id", "issue_date"], unique=False
    )
    op.create_index("ix_invoice_company_status", "invoices", ["company_id", "status"], unique=False)
    op.create_index(op.f("ix_invoices_created_at"), "invoices", ["created_at"], unique=False)
    op.create_index(op.f("ix_invoices_deleted_at"), "invoices", ["deleted_at"], unique=False)
    op.create_index(op.f("ix_invoices_updated_at"), "invoices", ["updated_at"], unique=False)
    op.create_foreign_key(
        op.f("fk__invoices__order_id__orders"),
        "invoices",
        "orders",
        ["order_id"],
        ["id"],
        ondelete="CASCADE",
    )
    (op.add_column("messages", sa.Column("channel", message_channel_enum, nullable=False)),)
    (
        op.add_column(
            "messages", sa.Column("provider_message_id", sa.String(length=255), nullable=True)
        ),
    )
    (
        op.add_column(
            "messages", sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True)
        ),
    )
    (op.add_column("messages", sa.Column("error_code", sa.String(length=64), nullable=True)),)
    (op.add_column("messages", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True)),)
    (op.add_column("messages", sa.Column("deleted_by", sa.Integer(), nullable=True)),)
    (op.add_column("messages", sa.Column("delete_reason", sa.Text(), nullable=True)),)
    # --- messages.status: РјРёРіСЂР°С†РёСЏ РЅР° enum message_status
    # 0) РѕРїСЂРµРґРµР»СЏРµРј С†РµР»РµРІРѕР№ enum-РѕР±СЉРµРєС‚ Р±РµР· СЃРѕР·РґР°РЅРёСЏ С‚РёРїР° (РѕРЅ СѓР¶Рµ СЃРѕР·РґР°РЅ РІС‹С€Рµ)
    message_status_enum = sa.Enum(
        "PENDING",
        "QUEUED",
        "SENT",
        "DELIVERED",
        "FAILED",
        "OPENED",
        "CLICKED",
        name="message_status",
        create_type=False,
    )

    # 1) СЃРЅРёРјР°РµРј DEFAULT (РµСЃР»Рё РµСЃС‚СЊ), С‡С‚РѕР±С‹ РЅРµ РјРµС€Р°Р» РїСЂРёРІРµРґРµРЅРёСЋ С‚РёРїРѕРІ
    op.execute("ALTER TABLE messages ALTER COLUMN status DROP DEFAULT")

    # 2) РЅРѕСЂРјР°Р»РёР·СѓРµРј РґР°РЅРЅС‹Рµ Рє РґРѕРїСѓСЃС‚РёРјС‹Рј РјРµС‚РєР°Рј enum
    op.execute(
        """
    UPDATE messages
       SET status = 'PENDING'
     WHERE status IS NULL
        OR status::text NOT IN ('PENDING','QUEUED','SENT','DELIVERED','FAILED','OPENED','CLICKED')
    """
    )

    # 3) РјРµРЅСЏРµРј С‚РёРї РЅР° С†РµР»РµРІРѕР№ enum СЃ СЏРІРЅС‹Рј USING (РџРћРњР•Р§Р•РќРћ Р’РђР–РќРћ)
    op.alter_column(
        "messages",
        "status",
        existing_type=postgresql.ENUM(
            "PENDING", "SENT", "DELIVERED", "FAILED", name="messagestatus"
        ),
        type_=message_status_enum,
        postgresql_using="status::text::message_status",
        existing_nullable=False,
    )

    # 4) РІРѕР·РІСЂР°С‰Р°РµРј РЅСѓР¶РЅС‹Р№ DEFAULT
    op.execute("ALTER TABLE messages ALTER COLUMN status SET DEFAULT 'PENDING'::message_status")

    op.alter_column(
        "messages",
        "sent_at",
        existing_type=postgresql.TIMESTAMP(),
        type_=sa.DateTime(timezone=True),
        existing_nullable=True,
    )
    op.create_index(
        "ix_message_campaign_status_channel",
        "messages",
        ["campaign_id", "status", "channel"],
        unique=False,
    )
    op.create_index(op.f("ix_messages_channel"), "messages", ["channel"], unique=False)
    op.create_index(op.f("ix_messages_deleted_at"), "messages", ["deleted_at"], unique=False)
    op.create_index(op.f("ix_messages_deleted_by"), "messages", ["deleted_by"], unique=False)
    op.create_index(op.f("ix_messages_error_code"), "messages", ["error_code"], unique=False)
    op.create_index(
        op.f("ix_messages_provider_message_id"), "messages", ["provider_message_id"], unique=False
    )
    op.create_index(op.f("ix_messages_recipient"), "messages", ["recipient"], unique=False)
    op.drop_constraint(op.f("fk_messages_campaign_id_campaigns"), "messages", type_="foreignkey")
    op.create_foreign_key(
        op.f("fk__messages__campaign_id__campaigns"),
        "messages",
        "campaigns",
        ["campaign_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.drop_column("messages", "updated_at")
    op.drop_column("messages", "created_at")
    (
        op.add_column(
            "order_items",
            sa.Column("cost_price", sa.Numeric(precision=14, scale=2), nullable=False),
        ),
    )
    op.create_index("ix_order_items_order_sku", "order_items", ["order_id", "sku"], unique=False)
    op.create_index(op.f("ix_order_items_sku"), "order_items", ["sku"], unique=False)
    op.drop_column("order_items", "updated_at")
    op.drop_column("order_items", "created_at")
    op.alter_column(
        "orders",
        "created_at",
        existing_type=postgresql.TIMESTAMP(timezone=True),
        server_default=None,
        type_=sa.DateTime(),
        existing_nullable=False,
    )
    op.alter_column(
        "orders",
        "updated_at",
        existing_type=postgresql.TIMESTAMP(timezone=True),
        server_default=None,
        type_=sa.DateTime(),
        existing_nullable=False,
    )
    op.alter_column(
        "orders",
        "tax_amount",
        existing_type=sa.NUMERIC(precision=14, scale=2),
        server_default=None,
        existing_nullable=False,
    )
    op.alter_column(
        "orders",
        "shipping_amount",
        existing_type=sa.NUMERIC(precision=14, scale=2),
        server_default=None,
        existing_nullable=False,
    )
    op.alter_column(
        "orders",
        "discount_amount",
        existing_type=sa.NUMERIC(precision=14, scale=2),
        server_default=None,
        existing_nullable=False,
    )
    op.alter_column(
        "orders",
        "currency",
        existing_type=sa.VARCHAR(length=8),
        server_default=None,
        existing_nullable=False,
    )
    op.create_index("ix_orders_company_status", "orders", ["company_id", "status"], unique=False)
    op.create_index(op.f("ix_orders_created_at"), "orders", ["created_at"], unique=False)
    op.create_index("ix_orders_source_created", "orders", ["source", "created_at"], unique=False)
    op.create_index(op.f("ix_orders_updated_at"), "orders", ["updated_at"], unique=False)
    op.create_unique_constraint("uq_orders_order_number", "orders", ["order_number"])
    op.add_column("otp_codes", sa.Column("user_id", sa.Integer(), nullable=True))
    op.add_column("otp_codes", sa.Column("verified_at", sa.DateTime(), nullable=True))
    op.alter_column(
        "otp_codes",
        "phone",
        existing_type=sa.VARCHAR(length=20),
        type_=sa.String(length=32),
        existing_nullable=False,
    )
    op.alter_column(
        "otp_codes",
        "code",
        existing_type=sa.VARCHAR(length=6),
        type_=sa.String(length=16),
        existing_nullable=False,
    )
    op.alter_column(
        "otp_codes",
        "purpose",
        existing_type=sa.VARCHAR(length=50),
        type_=sa.String(length=32),
        existing_nullable=False,
    )
    op.drop_index(op.f("ix_otp_codes_phone"), table_name="otp_codes")
    op.create_index("ix_otpcode_created_at", "otp_codes", ["created_at"], unique=False)
    op.create_index("ix_otpcode_expires_at", "otp_codes", ["expires_at"], unique=False)
    op.create_index("ix_otpcode_is_used", "otp_codes", ["is_used"], unique=False)
    op.create_index("ix_otpcode_phone", "otp_codes", ["phone"], unique=False)
    op.create_index(
        "ix_otpcode_phone_purpose_created",
        "otp_codes",
        ["phone", "purpose", "created_at"],
        unique=False,
    )
    op.create_index("ix_otpcode_purpose", "otp_codes", ["purpose"], unique=False)
    op.create_unique_constraint(
        "uq_otp_code_phone_purpose", "otp_codes", ["code", "phone", "purpose"]
    )
    op.create_foreign_key(
        op.f("fk__otp_codes__user_id__users"),
        "otp_codes",
        "users",
        ["user_id"],
        ["id"],
        ondelete="SET NULL",
    )
    (op.add_column("payment_refunds", sa.Column("uuid", sa.UUID(), nullable=False)),)
    (op.add_column("payment_refunds", sa.Column("version", sa.Integer(), nullable=False)),)
    (op.add_column("payment_refunds", sa.Column("failed_at", sa.DateTime(), nullable=True)),)
    op.alter_column(
        "payment_refunds",
        "created_at",
        existing_type=postgresql.TIMESTAMP(timezone=True),
        type_=sa.DateTime(),
        existing_nullable=False,
        existing_server_default=sa.text("now()"),
    )
    op.alter_column(
        "payment_refunds",
        "updated_at",
        existing_type=postgresql.TIMESTAMP(timezone=True),
        type_=sa.DateTime(),
        existing_nullable=False,
        existing_server_default=sa.text("now()"),
    )
    op.alter_column(
        "payment_refunds",
        "currency",
        existing_type=sa.VARCHAR(length=8),
        server_default=None,
        existing_nullable=False,
    )
    op.alter_column(
        "payment_refunds",
        "processed_at",
        existing_type=postgresql.TIMESTAMP(timezone=True),
        type_=sa.DateTime(),
        existing_nullable=True,
    )
    op.alter_column(
        "payment_refunds",
        "completed_at",
        existing_type=postgresql.TIMESTAMP(timezone=True),
        type_=sa.DateTime(),
        existing_nullable=True,
    )
    # РќРѕСЂРјР°Р»РёР·СѓРµРј Р·РЅР°С‡РµРЅРёСЏ РїРµСЂРµРґ РєР°СЃС‚РѕРј: NULL/РїСѓСЃС‚С‹Рµ в†’ '{}'
    op.execute(
        """
        UPDATE payment_refunds
           SET provider_data = '{}'
         WHERE provider_data IS NULL
            OR (provider_data::text IS NOT NULL AND btrim(provider_data::text) = '');
    """
    )

    # (РћРїС†РёРѕРЅР°Р»СЊРЅРѕ, РµСЃР»Рё РІРѕР·РјРѕР¶РµРЅ В«РіСЂСЏР·РЅС‹Р№В» JSON Рё PG в‰Ґ 14)
    op.execute(
        """
        UPDATE payment_refunds
           SET provider_data = '{}'
         WHERE provider_data IS NOT NULL
           AND NOT (provider_data IS JSON);
    """
    )

    # РљР°СЃС‚ РІ JSONB СЃ СЏРІРЅС‹Рј USING Рё Р·Р°С‰РёС‚РѕР№ РѕС‚ РїСѓСЃС‚С‹С… СЃС‚СЂРѕРє
    op.alter_column(
        "payment_refunds",
        "provider_data",
        existing_type=sa.TEXT(),  # РµСЃР»Рё СЂР°РЅСЊС€Рµ Р±С‹Р»Рѕ JSON/JSONB вЂ” РѕСЃС‚Р°РІРёС‚СЊ РєР°Рє РµСЃС‚СЊ РЅРµ РєСЂРёС‚РёС‡РЅРѕ
        type_=postgresql.JSONB(astext_type=sa.Text()),
        existing_nullable=True,
        postgresql_using="""
            CASE
                WHEN provider_data IS NULL THEN '{}'::jsonb
                WHEN btrim(provider_data::text) = '' THEN '{}'::jsonb
                ELSE provider_data::jsonb
            END
        """,
        server_default=None,
    )

    op.create_index(
        op.f("ix_payment_refunds_created_at"), "payment_refunds", ["created_at"], unique=False
    )
    op.create_index(op.f("ix_payment_refunds_id"), "payment_refunds", ["id"], unique=False)
    op.create_index(
        "ix_payment_refunds_payment_status",
        "payment_refunds",
        ["payment_id", "status"],
        unique=False,
    )
    op.create_index(
        "ix_payment_refunds_provider_data_gin",
        "payment_refunds",
        ["provider_data"],
        unique=False,
        postgresql_using="gin",
    )
    op.create_index(
        op.f("ix_payment_refunds_updated_at"), "payment_refunds", ["updated_at"], unique=False
    )
    op.create_index(op.f("ix_payment_refunds_uuid"), "payment_refunds", ["uuid"], unique=True)
    (op.add_column("payments", sa.Column("uuid", sa.UUID(), nullable=False)),)
    (op.add_column("payments", sa.Column("version", sa.Integer(), nullable=False)),)
    (op.add_column("payments", sa.Column("order_id", sa.Integer(), nullable=False)),)
    (op.add_column("payments", sa.Column("payment_number", sa.String(length=64), nullable=False)),)
    (op.add_column("payments", sa.Column("external_id", sa.String(length=128), nullable=True)),)
    (
        op.add_column(
            "payments", sa.Column("provider_invoice_id", sa.String(length=128), nullable=True)
        ),
    )
    (op.add_column("payments", sa.Column("provider", paymentprovider_enum, nullable=False)),)
    (op.add_column("payments", sa.Column("method", paymentmethod_enum, nullable=False)),)
    (
        op.add_column(
            "payments", sa.Column("fee_amount", sa.Numeric(precision=14, scale=2), nullable=False)
        ),
    )
    (
        op.add_column(
            "payments",
            sa.Column("refunded_amount", sa.Numeric(precision=14, scale=2), nullable=False),
        ),
    )
    (
        op.add_column(
            "payments",
            sa.Column(
                "refund_reason_history", postgresql.JSONB(astext_type=sa.Text()), nullable=True
            ),
        ),
    )
    (
        op.add_column(
            "payments",
            sa.Column("provider_data", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        ),
    )
    (op.add_column("payments", sa.Column("receipt_url", sa.String(length=1024), nullable=True)),)
    (op.add_column("payments", sa.Column("receipt_number", sa.String(length=64), nullable=True)),)
    (op.add_column("payments", sa.Column("processed_at", sa.DateTime(), nullable=True)),)
    (op.add_column("payments", sa.Column("confirmed_at", sa.DateTime(), nullable=True)),)
    (op.add_column("payments", sa.Column("failed_at", sa.DateTime(), nullable=True)),)
    (op.add_column("payments", sa.Column("cancelled_at", sa.DateTime(), nullable=True)),)
    (op.add_column("payments", sa.Column("description", sa.Text(), nullable=True)),)
    (op.add_column("payments", sa.Column("customer_ip", postgresql.INET(), nullable=True)),)
    (op.add_column("payments", sa.Column("user_agent", sa.Text(), nullable=True)),)
    (op.add_column("payments", sa.Column("failure_reason", sa.String(length=255), nullable=True)),)
    (op.add_column("payments", sa.Column("failure_code", sa.String(length=32), nullable=True)),)
    (op.add_column("payments", sa.Column("is_test", sa.Boolean(), nullable=False)),)
    # === PAYMENTS: РїРѕРґРіРѕС‚РѕРІРєР° РґР°РЅРЅС‹С… РїРµСЂРµРґ РєР°СЃС‚Р°РјРё ===

    # 1) created_at / updated_at Р±С‹Р»Рё VARCHAR(40) вЂ” СѓР±РёСЂР°РµРј РїСѓСЃС‚С‹Рµ СЃС‚СЂРѕРєРё
    op.execute(
        """
        UPDATE payments
           SET created_at = NULL
         WHERE created_at IS NOT NULL AND btrim(created_at) = '';
    """
    )
    op.execute(
        """
        UPDATE payments
           SET updated_at = NULL
         WHERE updated_at IS NOT NULL AND btrim(updated_at) = '';
    """
    )

    # (РћРїС†РёРѕРЅР°Р»СЊРЅРѕ, РµСЃР»Рё РїРѕРґРѕР·СЂРµРІР°РµС‚Рµ В«РіСЂСЏР·РЅС‹РµВ» СЃС‚СЂРѕРєРё РґР°С‚):
    # РџСЂРµРІСЂР°С‚РёРј В«СЏРІРЅРѕ РЅРµ ISO-РґР°С‚СѓВ» РІ NULL (РіСЂСѓР±Р°СЏ, РЅРѕ РїРѕР»РµР·РЅР°СЏ СЃС‚СЂР°С…РѕРІРєР°)
    op.execute(
        r"""
        UPDATE payments
           SET created_at = NULL
         WHERE created_at IS NOT NULL
           AND NOT (created_at ~ '^\s*\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(:\d{2}(\.\d{1,6})?)?');
    """
    )
    op.execute(
        r"""
        UPDATE payments
           SET updated_at = NULL
         WHERE updated_at IS NOT NULL
           AND NOT (updated_at ~ '^\s*\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(:\d{2}(\.\d{1,6})?)?');
    """
    )

    # 2) status: РїРµСЂРµРґ ENUM вЂ” РЅРѕСЂРјР°Р»РёР·СѓРµРј Рё РїРѕРґСЃС‚СЂР°С…СѓРµРј РЅРµРІР°Р»РёРґРЅС‹Рµ Р·РЅР°С‡РµРЅРёСЏ
    # РџСЂРёРІРµРґС‘Рј Рє РІРµСЂС…РЅРµРјСѓ СЂРµРіРёСЃС‚СЂСѓ Рё РѕР±СЂРµР¶РµРј РїСЂРѕР±РµР»С‹
    op.execute("UPDATE payments SET status = upper(btrim(status)) WHERE status IS NOT NULL;")

    # Р—РЅР°С‡РµРЅРёСЏ, РєРѕС‚РѕСЂС‹С… РЅРµС‚ РІ С†РµР»РµРІРѕРј ENUM, СЃРІРµРґС‘Рј Рє 'PENDING' (РёР»Рё РІС‹Р±РµСЂРёС‚Рµ РЅСѓР¶РЅС‹Р№ РІР°Рј РґРµС„РѕР»С‚)
    op.execute(
        """
        UPDATE payments
           SET status = 'PENDING'
         WHERE status IS NULL
            OR status NOT IN ('PENDING','AUTHORIZED','CAPTURED','SENT','DELIVERED','FAILED','REFUNDED','CHARGEBACK');
    """
    )

    # 3) amount: NUMERIC(18,6) -> NUMERIC(14,2) вЂ” Р°РєРєСѓСЂР°С‚РЅРѕ РѕРєСЂСѓРіР»РёРј РґРѕ 2 Р·РЅР°РєРѕРІ
    # Р•СЃР»Рё Р±РѕРёС‚РµСЃСЊ РїРµСЂРµРїРѕР»РЅРµРЅРёСЏ РїРѕ СЂР°Р·СЂСЏРґР°Рј вЂ” Р·Р°СЂР°РЅРµРµ РїСЂРѕРІРµСЂСЊС‚Рµ Рё Р·Р°С„РёРєСЃРёСЂСѓР№С‚Рµ out-of-range.
    op.execute("UPDATE payments SET amount = ROUND(amount, 2) WHERE amount IS NOT NULL;")

    # === PAYMENTS: СЃР°РјРё ALTER COLUMN СЃ СЏРІРЅС‹Рј USING ===

    # created_at -> TIMESTAMP WITHOUT TIME ZONE, UTC-naive
    op.alter_column(
        "payments",
        "created_at",
        existing_type=sa.VARCHAR(length=40),
        type_=sa.TIMESTAMP(timezone=False),
        existing_nullable=False,
        server_default=sa.text("timezone('utc', now())"),
        postgresql_using="""
            CASE
                WHEN created_at IS NULL THEN NULL
                ELSE created_at::timestamp
            END
        """,
    )

    # updated_at -> TIMESTAMP WITHOUT TIME ZONE, UTC-naive
    op.alter_column(
        "payments",
        "updated_at",
        existing_type=sa.VARCHAR(length=40),
        type_=sa.TIMESTAMP(timezone=False),
        existing_nullable=False,
        # РґР»СЏ updated_at РјРѕР¶РЅРѕ РѕСЃС‚Р°РІРёС‚СЊ С‚РѕС‚ Р¶Рµ default вЂ” РїСЂРё РЅР°Р»РёС‡РёРё ORM РІС‹ РІСЃС‘ СЂР°РІРЅРѕ Р±СѓРґРµС‚Рµ РѕР±РЅРѕРІР»СЏС‚СЊ РІ РїСЂРёР»РѕР¶РµРЅРёРё
        server_default=sa.text("timezone('utc', now())"),
        postgresql_using="""
            CASE
                WHEN updated_at IS NULL THEN NULL
                ELSE updated_at::timestamp
            END
        """,
    )

    op.alter_column(
        "payments",
        "status",
        existing_type=sa.VARCHAR(length=20),
        type_=paymentstatus_enum,
        existing_nullable=False,
        postgresql_using="status::paymentstatus",  # РєР»СЋС‡РµРІР°СЏ СЃС‚СЂРѕРєР°
    )

    # amount -> NUMERIC(14,2)
    op.alter_column(
        "payments",
        "amount",
        existing_type=sa.NUMERIC(precision=18, scale=6),
        type_=sa.Numeric(precision=14, scale=2),
        existing_nullable=False,
        postgresql_using="ROUND(amount, 2)",
    )

    # currency -> VARCHAR(8)
    op.alter_column(
        "payments",
        "currency",
        existing_type=sa.VARCHAR(length=10),
        type_=sa.String(length=8),
        existing_nullable=False,
    )

    op.drop_index(op.f("ix_payments_currency"), table_name="payments")
    op.drop_index(op.f("ix_payments_user_id"), table_name="payments")
    op.drop_index(op.f("ix_payments_wallet_account_id"), table_name="payments")
    op.create_index(op.f("ix_payments_created_at"), "payments", ["created_at"], unique=False)
    op.create_index(op.f("ix_payments_customer_id"), "payments", ["customer_id"], unique=False)
    op.create_index(op.f("ix_payments_customer_ip"), "payments", ["customer_ip"], unique=False)
    op.create_index(op.f("ix_payments_external_id"), "payments", ["external_id"], unique=False)
    op.create_index(op.f("ix_payments_id"), "payments", ["id"], unique=False)
    op.create_index(op.f("ix_payments_order_id"), "payments", ["order_id"], unique=False)
    op.create_index("ix_payments_order_status", "payments", ["order_id", "status"], unique=False)
    op.create_index(op.f("ix_payments_payment_number"), "payments", ["payment_number"], unique=True)
    op.create_index(
        "ix_payments_provider_data_gin",
        "payments",
        ["provider_data"],
        unique=False,
        postgresql_using="gin",
    )
    op.create_index(
        op.f("ix_payments_provider_invoice_id"), "payments", ["provider_invoice_id"], unique=True
    )
    op.create_index("ix_payments_provider_status", "payments", ["provider", "status"], unique=False)
    op.create_index(
        "ix_payments_refund_hist_gin",
        "payments",
        ["refund_reason_history"],
        unique=False,
        postgresql_using="gin",
    )
    op.create_index(op.f("ix_payments_updated_at"), "payments", ["updated_at"], unique=False)
    op.create_index(op.f("ix_payments_uuid"), "payments", ["uuid"], unique=True)
    op.create_foreign_key(
        op.f("fk__payments__order_id__orders"),
        "payments",
        "orders",
        ["order_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.drop_column("payments", "user_id")
    op.drop_column("payments", "reference")
    op.drop_column("payments", "wallet_account_id")
    op.drop_column("payments", "refund_amount")
    (
        op.add_column(
            "product_stocks",
            sa.Column("cost_price", sa.Numeric(precision=14, scale=2), nullable=True),
        ),
    )
    (op.add_column("product_stocks", sa.Column("last_restocked_at", sa.DateTime(), nullable=True)),)
    (op.add_column("product_stocks", sa.Column("is_archived", sa.Boolean(), nullable=False)),)
    (op.add_column("product_stocks", sa.Column("archived_at", sa.DateTime(), nullable=True)),)
    op.alter_column(
        "product_stocks",
        "quantity",
        existing_type=sa.INTEGER(),
        server_default=None,
        existing_nullable=False,
    )
    op.alter_column(
        "product_stocks",
        "reserved_quantity",
        existing_type=sa.INTEGER(),
        server_default=None,
        existing_nullable=False,
    )
    op.alter_column(
        "product_stocks",
        "min_quantity",
        existing_type=sa.INTEGER(),
        server_default=None,
        existing_nullable=False,
    )
    op.alter_column(
        "product_stocks",
        "created_at",
        existing_type=postgresql.TIMESTAMP(timezone=True),
        server_default=None,
        type_=sa.DateTime(),
        existing_nullable=False,
    )
    op.alter_column(
        "product_stocks",
        "updated_at",
        existing_type=postgresql.TIMESTAMP(timezone=True),
        server_default=None,
        type_=sa.DateTime(),
        existing_nullable=False,
    )
    op.create_index(
        op.f("ix_product_stocks_created_at"), "product_stocks", ["created_at"], unique=False
    )
    op.create_index(op.f("ix_product_stocks_id"), "product_stocks", ["id"], unique=False)
    op.create_index(
        op.f("ix_product_stocks_is_archived"), "product_stocks", ["is_archived"], unique=False
    )
    op.create_index(
        op.f("ix_product_stocks_updated_at"), "product_stocks", ["updated_at"], unique=False
    )
    op.create_index(
        "ix_stock_low", "product_stocks", ["warehouse_id", "min_quantity"], unique=False
    )
    op.create_index(
        "ix_stock_product_warehouse_qty",
        "product_stocks",
        ["product_id", "warehouse_id", "quantity"],
        unique=False,
    )
    (op.add_column("product_variants", sa.Column("deleted_at", sa.DateTime(), nullable=True)),)
    (
        op.add_column(
            "product_variants",
            sa.Column("version", sa.Integer(), server_default=sa.text("0"), nullable=False),
        ),
    )
    op.alter_column("product_variants", "sku", existing_type=sa.VARCHAR(length=100), nullable=True)
    op.alter_column(
        "product_variants",
        "price",
        existing_type=sa.NUMERIC(precision=10, scale=2),
        type_=sa.Numeric(precision=14, scale=2),
        existing_nullable=True,
    )
    op.alter_column(
        "product_variants",
        "cost_price",
        existing_type=sa.NUMERIC(precision=10, scale=2),
        type_=sa.Numeric(precision=14, scale=2),
        existing_nullable=True,
    )
    op.alter_column(
        "product_variants",
        "sale_price",
        existing_type=sa.NUMERIC(precision=10, scale=2),
        type_=sa.Numeric(precision=14, scale=2),
        existing_nullable=True,
    )
    op.alter_column(
        "product_variants",
        "stock_quantity",
        existing_type=sa.INTEGER(),
        server_default=sa.text("0"),
        existing_nullable=False,
    )
    op.alter_column(
        "product_variants",
        "is_active",
        existing_type=sa.BOOLEAN(),
        server_default=sa.text("true"),
        existing_nullable=False,
    )
    op.alter_column(
        "product_variants",
        "created_at",
        existing_type=postgresql.TIMESTAMP(),
        server_default=sa.text("now()"),
        existing_nullable=False,
    )
    op.drop_index(op.f("ix_product_variants_variant_name"), table_name="product_variants")
    op.drop_constraint(op.f("uq_variant_sku"), "product_variants", type_="unique")
    op.drop_index(op.f("ix_product_variants_sku"), table_name="product_variants")
    op.create_index(op.f("ix_product_variants_sku"), "product_variants", ["sku"], unique=False)
    op.create_index(
        op.f("ix_product_variants_deleted_at"), "product_variants", ["deleted_at"], unique=False
    )
    op.create_index(
        op.f("ix_product_variants_is_active"), "product_variants", ["is_active"], unique=False
    )
    op.create_index(op.f("ix_product_variants_name"), "product_variants", ["name"], unique=False)
    op.create_unique_constraint("uq_variant_product_sku", "product_variants", ["product_id", "sku"])
    op.drop_column("product_variants", "stock")
    op.drop_column("product_variants", "variant_name")
    op.alter_column("products", "name", existing_type=sa.VARCHAR(length=255), nullable=True)
    op.alter_column("products", "slug", existing_type=sa.VARCHAR(length=255), nullable=True)
    op.alter_column("products", "sku", existing_type=sa.VARCHAR(length=100), nullable=True)
    op.alter_column(
        "products",
        "price",
        existing_type=sa.NUMERIC(precision=10, scale=2),
        type_=sa.Numeric(precision=14, scale=2),
        nullable=True,
    )
    op.alter_column(
        "products",
        "cost_price",
        existing_type=sa.NUMERIC(precision=10, scale=2),
        type_=sa.Numeric(precision=14, scale=2),
        existing_nullable=True,
    )
    op.alter_column(
        "products",
        "sale_price",
        existing_type=sa.NUMERIC(precision=10, scale=2),
        type_=sa.Numeric(precision=14, scale=2),
        existing_nullable=True,
    )
    op.alter_column(
        "products",
        "stock_quantity",
        existing_type=sa.INTEGER(),
        server_default=sa.text("0"),
        existing_nullable=False,
    )
    op.alter_column(
        "products",
        "is_active",
        existing_type=sa.BOOLEAN(),
        server_default=sa.text("true"),
        existing_nullable=False,
    )
    op.alter_column(
        "products",
        "preorder_lead_days",
        existing_type=sa.INTEGER(),
        server_default=None,
        nullable=True,
    )
    op.alter_column(
        "products",
        "preorder_show_zero_stock",
        existing_type=sa.BOOLEAN(),
        server_default=sa.text("true"),
        existing_nullable=False,
    )
    op.alter_column(
        "products",
        "repriced_at",
        existing_type=postgresql.TIMESTAMP(timezone=True),
        type_=sa.DateTime(),
        existing_nullable=True,
    )
    op.alter_column(
        "products",
        "price_updated_at",
        existing_type=postgresql.TIMESTAMP(timezone=True),
        type_=sa.DateTime(),
        existing_nullable=True,
    )
    op.alter_column(
        "products",
        "extra",
        existing_type=postgresql.JSONB(astext_type=sa.Text()),
        server_default=None,
        type_=sa.Text(),
        nullable=True,
    )
    op.alter_column(
        "products",
        "deleted_at",
        existing_type=postgresql.TIMESTAMP(timezone=True),
        type_=sa.DateTime(),
        existing_nullable=True,
    )
    op.alter_column(
        "products",
        "updated_at",
        existing_type=postgresql.TIMESTAMP(),
        server_default=None,
        existing_nullable=False,
    )
    op.alter_column(
        "products",
        "version",
        existing_type=sa.INTEGER(),
        server_default=sa.text("0"),
        existing_nullable=False,
    )
    op.drop_index(op.f("ix_product_category_active"), table_name="products")
    op.drop_index(op.f("ix_product_stock"), table_name="products")
    op.drop_index(op.f("ix_product_price_active"), table_name="products")
    op.create_index(
        "ix_product_price_active", "products", ["company_id", "price", "is_active"], unique=False
    )
    op.drop_index(op.f("ix_products_sku"), table_name="products")
    op.create_index(op.f("ix_products_sku"), "products", ["sku"], unique=False)
    op.drop_index(op.f("ix_products_slug"), table_name="products")
    op.create_index(op.f("ix_products_slug"), "products", ["slug"], unique=False)
    op.create_index(
        "ix_kaspi_company_status", "products", ["company_id", "kaspi_status"], unique=False
    )
    op.create_index(
        "ix_product_company_active", "products", ["company_id", "is_active"], unique=False
    )
    op.create_index(
        "ix_product_company_category_active_del",
        "products",
        ["company_id", "category_id", "is_active", "deleted_at"],
        unique=False,
    )
    op.create_index(
        "ix_product_company_featured", "products", ["company_id", "is_featured"], unique=False
    )
    op.create_index("ix_product_search_name", "products", ["company_id", "name"], unique=False)
    op.create_index(
        "ix_product_search_name_sku", "products", ["company_id", "name", "sku"], unique=False
    )
    op.create_index(op.f("ix_products_deleted_at"), "products", ["deleted_at"], unique=False)
    op.create_index(op.f("ix_products_is_featured"), "products", ["is_featured"], unique=False)
    op.create_index(op.f("ix_products_kaspi_status"), "products", ["kaspi_status"], unique=False)
    op.create_index(op.f("ix_products_price"), "products", ["price"], unique=False)
    op.create_index(
        op.f("ix_products_price_updated_at"), "products", ["price_updated_at"], unique=False
    )
    op.create_index(op.f("ix_products_repriced_at"), "products", ["repriced_at"], unique=False)
    op.create_foreign_key(
        op.f("fk__products__category_id__categories"),
        "products",
        "categories",
        ["category_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.drop_column("products", "width")
    op.drop_column("products", "length")
    op.drop_column("products", "weight")
    op.drop_column("products", "is_digital")
    op.drop_column("products", "height")
    (op.add_column("stock_movements", sa.Column("product_id", sa.Integer(), nullable=True)),)
    (op.add_column("stock_movements", sa.Column("order_id", sa.Integer(), nullable=True)),)
    (op.add_column("stock_movements", sa.Column("is_archived", sa.Boolean(), nullable=False)),)
    (op.add_column("stock_movements", sa.Column("archived_at", sa.DateTime(), nullable=True)),)
    op.alter_column("stock_movements", "stock_id", existing_type=sa.INTEGER(), nullable=True)
    op.alter_column(
        "stock_movements",
        "created_at",
        existing_type=postgresql.TIMESTAMP(timezone=True),
        server_default=None,
        type_=sa.DateTime(),
        existing_nullable=False,
    )
    op.alter_column(
        "stock_movements",
        "updated_at",
        existing_type=postgresql.TIMESTAMP(timezone=True),
        server_default=None,
        type_=sa.DateTime(),
        existing_nullable=False,
    )
    op.create_index("ix_movements_order", "stock_movements", ["order_id"], unique=False)
    op.create_index(
        "ix_movements_product_type",
        "stock_movements",
        ["product_id", "movement_type"],
        unique=False,
    )
    op.create_index(
        "ix_movements_stock_created", "stock_movements", ["stock_id", "created_at"], unique=False
    )
    op.create_index(op.f("ix_stock_movements_id"), "stock_movements", ["id"], unique=False)
    op.create_index(
        op.f("ix_stock_movements_is_archived"), "stock_movements", ["is_archived"], unique=False
    )
    op.create_index(
        op.f("ix_stock_movements_order_id"), "stock_movements", ["order_id"], unique=False
    )
    op.create_index(
        op.f("ix_stock_movements_product_id"), "stock_movements", ["product_id"], unique=False
    )
    op.create_foreign_key(
        op.f("fk__stock_movements__order_id__orders"),
        "stock_movements",
        "orders",
        ["order_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        op.f("fk__stock_movements__product_id__products"),
        "stock_movements",
        "products",
        ["product_id"],
        ["id"],
        ondelete="CASCADE",
    )
    (op.add_column("subscriptions", sa.Column("grace_period_days", sa.Integer(), nullable=False)),)
    (op.add_column("subscriptions", sa.Column("deleted_at", sa.DateTime(), nullable=True)),)
    op.alter_column(
        "subscriptions",
        "billing_cycle",
        existing_type=sa.VARCHAR(length=32),
        server_default=None,
        existing_nullable=False,
    )
    op.alter_column(
        "subscriptions",
        "price",
        existing_type=sa.NUMERIC(precision=10, scale=2),
        type_=sa.Numeric(precision=14, scale=2),
        existing_nullable=False,
    )
    op.alter_column(
        "subscriptions",
        "currency",
        existing_type=sa.VARCHAR(length=8),
        server_default=None,
        existing_nullable=False,
    )
    op.alter_column(
        "subscriptions",
        "auto_renew",
        existing_type=sa.BOOLEAN(),
        server_default=None,
        existing_nullable=False,
    )
    op.alter_column(
        "subscriptions",
        "trial_used",
        existing_type=sa.BOOLEAN(),
        server_default=None,
        existing_nullable=False,
    )
    op.alter_column(
        "subscriptions",
        "created_at",
        existing_type=postgresql.TIMESTAMP(timezone=True),
        server_default=None,
        type_=sa.DateTime(),
        existing_nullable=False,
    )
    op.alter_column(
        "subscriptions",
        "updated_at",
        existing_type=postgresql.TIMESTAMP(timezone=True),
        server_default=None,
        type_=sa.DateTime(),
        existing_nullable=False,
    )
    op.create_index(
        "ix_subscription_company_status", "subscriptions", ["company_id", "status"], unique=False
    )
    op.create_index(
        op.f("ix_subscriptions_created_at"), "subscriptions", ["created_at"], unique=False
    )
    op.create_index(
        op.f("ix_subscriptions_deleted_at"), "subscriptions", ["deleted_at"], unique=False
    )
    op.create_index(
        op.f("ix_subscriptions_updated_at"), "subscriptions", ["updated_at"], unique=False
    )
    (op.add_column("user_sessions", sa.Column("terminated_at", sa.DateTime(), nullable=True)),)
    (op.add_column("user_sessions", sa.Column("deleted_at", sa.DateTime(), nullable=True)),)
    op.alter_column(
        "user_sessions",
        "is_active",
        existing_type=sa.BOOLEAN(),
        server_default=sa.text("true"),
        existing_nullable=False,
    )
    op.alter_column(
        "user_sessions",
        "created_at",
        existing_type=postgresql.TIMESTAMP(),
        server_default=sa.text("now()"),
        existing_nullable=False,
    )
    op.create_index(
        "ix_user_sessions_active_user", "user_sessions", ["is_active", "user_id"], unique=False
    )
    op.create_index(
        op.f("ix_user_sessions_deleted_at"), "user_sessions", ["deleted_at"], unique=False
    )
    op.create_index(
        op.f("ix_user_sessions_updated_at"), "user_sessions", ["updated_at"], unique=False
    )
    op.create_index(
        "ix_user_sessions_user_expires", "user_sessions", ["user_id", "expires_at"], unique=False
    )
    op.create_foreign_key(
        op.f("fk__user_sessions__user_id__users"),
        "user_sessions",
        "users",
        ["user_id"],
        ["id"],
        ondelete="CASCADE",
    )
    (op.add_column("users", sa.Column("modified_at", sa.DateTime(), nullable=True)),)
    (
        op.add_column(
            "users", sa.Column("version", sa.Integer(), server_default=sa.text("0"), nullable=False)
        ),
    )
    op.alter_column(
        "users",
        "username",
        existing_type=sa.VARCHAR(length=255),
        type_=sa.String(length=50),
        nullable=True,
    )
    op.alter_column(
        "users",
        "hashed_password",
        existing_type=sa.VARCHAR(length=255),
        server_default=sa.text("''"),
        existing_nullable=False,
    )
    op.alter_column(
        "users",
        "is_active",
        existing_type=sa.BOOLEAN(),
        server_default=sa.text("true"),
        existing_nullable=False,
    )
    op.alter_column(
        "users",
        "is_verified",
        existing_type=sa.BOOLEAN(),
        server_default=sa.text("false"),
        existing_nullable=False,
    )
    op.alter_column(
        "users",
        "is_superuser",
        existing_type=sa.BOOLEAN(),
        server_default=sa.text("false"),
        existing_nullable=False,
    )
    op.alter_column(
        "users",
        "failed_login_attempts",
        existing_type=sa.INTEGER(),
        server_default=sa.text("0"),
        existing_nullable=False,
    )
    op.alter_column(
        "users",
        "locked_at",
        existing_type=postgresql.TIMESTAMP(timezone=True),
        type_=sa.DateTime(),
        existing_nullable=True,
    )
    op.alter_column(
        "users",
        "deleted_at",
        existing_type=postgresql.TIMESTAMP(timezone=True),
        type_=sa.DateTime(),
        existing_nullable=True,
    )
    op.alter_column(
        "users",
        "created_at",
        existing_type=postgresql.TIMESTAMP(),
        server_default=sa.text("now()"),
        existing_nullable=False,
    )
    op.drop_constraint(op.f("uq_user_email"), "users", type_="unique")
    op.drop_constraint(op.f("uq_user_phone"), "users", type_="unique")
    op.drop_constraint(op.f("uq_user_username"), "users", type_="unique")
    op.create_index("ix_users_active_role", "users", ["is_active", "role"], unique=False)
    op.create_index("ix_users_company_active", "users", ["company_id", "is_active"], unique=False)
    op.create_index(op.f("ix_users_deleted_at"), "users", ["deleted_at"], unique=False)
    op.create_index("ix_users_login_fields", "users", ["username", "phone", "email"], unique=False)
    op.create_index(
        "ix_users_username_email_lower",
        "users",
        [sa.literal_column("lower(username)"), sa.literal_column("lower(email)")],
        unique=False,
    )
    op.drop_constraint(op.f("fk_users_company_id"), "users", type_="foreignkey")
    op.create_foreign_key(
        op.f("fk__users__company_id__companies"),
        "users",
        "companies",
        ["company_id"],
        ["id"],
        ondelete="SET NULL",
    )
    (op.add_column("wallet_balances", sa.Column("version_id", sa.Integer(), nullable=False)),)
    (op.add_column("wallet_balances", sa.Column("deleted_at", sa.DateTime(), nullable=True)),)
    op.alter_column(
        "wallet_balances",
        "balance",
        existing_type=sa.NUMERIC(precision=10, scale=2),
        server_default=None,
        type_=sa.Numeric(precision=14, scale=2),
        existing_nullable=False,
    )
    op.alter_column(
        "wallet_balances",
        "currency",
        existing_type=sa.VARCHAR(length=8),
        server_default=None,
        existing_nullable=False,
    )
    op.alter_column(
        "wallet_balances",
        "credit_limit",
        existing_type=sa.NUMERIC(precision=10, scale=2),
        server_default=None,
        type_=sa.Numeric(precision=14, scale=2),
        existing_nullable=True,
    )
    op.alter_column(
        "wallet_balances",
        "auto_topup_enabled",
        existing_type=sa.BOOLEAN(),
        server_default=None,
        existing_nullable=False,
    )
    op.alter_column(
        "wallet_balances",
        "auto_topup_threshold",
        existing_type=sa.NUMERIC(precision=10, scale=2),
        type_=sa.Numeric(precision=14, scale=2),
        existing_nullable=True,
    )
    op.alter_column(
        "wallet_balances",
        "auto_topup_amount",
        existing_type=sa.NUMERIC(precision=10, scale=2),
        type_=sa.Numeric(precision=14, scale=2),
        existing_nullable=True,
    )
    op.alter_column(
        "wallet_balances",
        "created_at",
        existing_type=postgresql.TIMESTAMP(timezone=True),
        server_default=None,
        type_=sa.DateTime(),
        existing_nullable=False,
    )
    op.alter_column(
        "wallet_balances",
        "updated_at",
        existing_type=postgresql.TIMESTAMP(timezone=True),
        server_default=None,
        type_=sa.DateTime(),
        existing_nullable=False,
    )
    op.drop_constraint(op.f("wallet_balances_company_id_key"), "wallet_balances", type_="unique")
    op.drop_index(op.f("ix_wallet_balances_company_id"), table_name="wallet_balances")
    op.create_index(
        op.f("ix_wallet_balances_company_id"), "wallet_balances", ["company_id"], unique=True
    )
    op.create_index(
        op.f("ix_wallet_balances_created_at"), "wallet_balances", ["created_at"], unique=False
    )
    op.create_index(
        op.f("ix_wallet_balances_deleted_at"), "wallet_balances", ["deleted_at"], unique=False
    )
    op.create_index(
        op.f("ix_wallet_balances_updated_at"), "wallet_balances", ["updated_at"], unique=False
    )
    op.add_column("wallet_transactions", sa.Column("deleted_at", sa.DateTime(), nullable=True))
    op.alter_column(
        "wallet_transactions",
        "amount",
        existing_type=sa.NUMERIC(precision=10, scale=2),
        type_=sa.Numeric(precision=14, scale=2),
        existing_nullable=False,
    )
    op.alter_column(
        "wallet_transactions",
        "balance_before",
        existing_type=sa.NUMERIC(precision=10, scale=2),
        type_=sa.Numeric(precision=14, scale=2),
        existing_nullable=False,
    )
    op.alter_column(
        "wallet_transactions",
        "balance_after",
        existing_type=sa.NUMERIC(precision=10, scale=2),
        type_=sa.Numeric(precision=14, scale=2),
        existing_nullable=False,
    )
    op.alter_column(
        "wallet_transactions",
        "created_at",
        existing_type=postgresql.TIMESTAMP(timezone=True),
        server_default=None,
        existing_nullable=False,
    )
    op.alter_column(
        "wallet_transactions",
        "updated_at",
        existing_type=postgresql.TIMESTAMP(timezone=True),
        server_default=None,
        type_=sa.DateTime(),
        existing_nullable=False,
    )
    op.create_index(
        "ix_wallet_transaction_wallet_type",
        "wallet_transactions",
        ["wallet_id", "transaction_type"],
        unique=False,
    )
    op.create_index(
        op.f("ix_wallet_transactions_created_at"),
        "wallet_transactions",
        ["created_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_wallet_transactions_deleted_at"),
        "wallet_transactions",
        ["deleted_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_wallet_transactions_updated_at"),
        "wallet_transactions",
        ["updated_at"],
        unique=False,
    )
    # --- WAREHOUSES: РЅРѕРІС‹Рµ РєРѕР»РѕРЅРєРё Р°СЂС…РёРІР°С†РёРё ---
    # Р”РѕР±Р°РІР»СЏРµРј is_archived c РІСЂРµРјРµРЅРЅС‹Рј default=false, С‡С‚РѕР±С‹ РЅРµ СѓРїР°СЃС‚СЊ РЅР° NOT NULL РїСЂРё СЃСѓС‰РµСЃС‚РІСѓСЋС‰РёС… СЃС‚СЂРѕРєР°С…
    op.add_column(
        "warehouses",
        sa.Column("is_archived", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    # РџРѕСЃР»Рµ Р·Р°РїРѕР»РЅРµРЅРёСЏ вЂ” РјРѕР¶РЅРѕ СѓР±СЂР°С‚СЊ default (РѕСЃС‚Р°РІРёРј РїРѕР»Рµ Р±РµР· РґРµС„РѕР»С‚Р°, РїСЂРёР»РѕР¶РµРЅРёРµ СЃР°РјРѕ Р±СѓРґРµС‚ Р·Р°РґР°РІР°С‚СЊ)
    op.alter_column("warehouses", "is_archived", server_default=None)

    op.add_column("warehouses", sa.Column("archived_at", sa.DateTime(), nullable=True))

    # --- WAREHOUSES: С‡РёСЃС‚РєР° Рё РєР°СЃС‚ СЂР°Р±РѕС‡РёС… С‡Р°СЃРѕРІ TEXT -> JSON ---
    # РџСЂРµРґРѕС‡РёСЃС‚РєР° РїСѓСЃС‚С‹С… Р·РЅР°С‡РµРЅРёР№
    op.execute(
        """
        UPDATE warehouses
           SET working_hours = '{}'
         WHERE working_hours IS NULL
            OR btrim(working_hours::text) = '';
    """
    )
    # Р•СЃР»Рё PG >= 14 Рё РІРѕР·РјРѕР¶РµРЅ В«РіСЂСЏР·РЅС‹Р№В» С‚РµРєСЃС‚ (РЅРµ РІР°Р»РёРґРЅС‹Р№ JSON), РјРѕР¶РЅРѕ РґРѕРїРѕР»РЅРёС‚РµР»СЊРЅРѕ:
    # op.execute("UPDATE warehouses SET working_hours='{}' WHERE working_hours IS NOT NULL AND NOT (working_hours IS JSON);")

    op.alter_column(
        "warehouses",
        "working_hours",
        existing_type=sa.TEXT(),
        type_=sa.JSON(),
        existing_nullable=True,
        postgresql_using="""
            CASE
                WHEN working_hours IS NULL THEN '{}'::json
                WHEN btrim(working_hours::text) = '' THEN '{}'::json
                ELSE working_hours::json
            END
        """,
    )

    # --- WAREHOUSES: Р±СѓР»РµРІС‹ С„Р»Р°РіРё (СѓР±СЂР°С‚СЊ РїСЂРµР¶РЅРёРµ DEFAULT-С‹, РµСЃР»Рё Р±С‹Р»Рё) ---
    op.alter_column(
        "warehouses",
        "is_active",
        existing_type=sa.BOOLEAN(),
        server_default=None,
        existing_nullable=False,
    )
    op.alter_column(
        "warehouses",
        "is_main",
        existing_type=sa.BOOLEAN(),
        server_default=None,
        existing_nullable=False,
    )

    # --- WAREHOUSES: timestamptz -> timestamp (UTC-naive) ---
    op.alter_column(
        "warehouses",
        "created_at",
        existing_type=postgresql.TIMESTAMP(timezone=True),
        type_=sa.TIMESTAMP(timezone=False),
        existing_nullable=False,
        server_default=sa.text("timezone('utc', now())"),
        postgresql_using="(created_at AT TIME ZONE 'UTC')",
    )
    op.alter_column(
        "warehouses",
        "updated_at",
        existing_type=postgresql.TIMESTAMP(timezone=True),
        type_=sa.TIMESTAMP(timezone=False),
        existing_nullable=False,
        server_default=sa.text("timezone('utc', now())"),
        postgresql_using="(updated_at AT TIME ZONE 'UTC')",
    )

    op.create_index(
        "ix_warehouses_company_active", "warehouses", ["company_id", "is_active"], unique=False
    )
    op.create_index(op.f("ix_warehouses_created_at"), "warehouses", ["created_at"], unique=False)
    op.create_index(op.f("ix_warehouses_id"), "warehouses", ["id"], unique=False)
    op.create_index(op.f("ix_warehouses_is_archived"), "warehouses", ["is_archived"], unique=False)
    op.create_index(op.f("ix_warehouses_updated_at"), "warehouses", ["updated_at"], unique=False)
    op.create_unique_constraint("uq_warehouse_company_code", "warehouses", ["company_id", "code"])
    # ### end Alembic commands ###

    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_constraint("uq_warehouse_company_code", "warehouses", type_="unique")
    op.drop_index(op.f("ix_warehouses_updated_at"), table_name="warehouses")
    op.drop_index(op.f("ix_warehouses_is_archived"), table_name="warehouses")
    op.drop_index(op.f("ix_warehouses_id"), table_name="warehouses")
    op.drop_index(op.f("ix_warehouses_created_at"), table_name="warehouses")
    op.drop_index("ix_warehouses_company_active", table_name="warehouses")
    op.alter_column(
        "warehouses",
        "updated_at",
        existing_type=sa.DateTime(),
        server_default=sa.text("now()"),
        type_=postgresql.TIMESTAMP(timezone=True),
        existing_nullable=False,
    )
    op.alter_column(
        "warehouses",
        "created_at",
        existing_type=sa.DateTime(),
        server_default=sa.text("now()"),
        type_=postgresql.TIMESTAMP(timezone=True),
        existing_nullable=False,
    )
    op.alter_column(
        "warehouses",
        "working_hours",
        existing_type=sa.JSON(),
        type_=sa.TEXT(),
        existing_nullable=True,
    )
    op.alter_column(
        "warehouses",
        "is_main",
        existing_type=sa.BOOLEAN(),
        server_default=sa.text("false"),
        existing_nullable=False,
    )
    op.alter_column(
        "warehouses",
        "is_active",
        existing_type=sa.BOOLEAN(),
        server_default=sa.text("true"),
        existing_nullable=False,
    )
    op.drop_column("warehouses", "archived_at")
    op.drop_column("warehouses", "is_archived")
    op.drop_index(op.f("ix_wallet_transactions_updated_at"), table_name="wallet_transactions")
    op.drop_index(op.f("ix_wallet_transactions_deleted_at"), table_name="wallet_transactions")
    op.drop_index(op.f("ix_wallet_transactions_created_at"), table_name="wallet_transactions")
    op.drop_index("ix_wallet_transaction_wallet_type", table_name="wallet_transactions")
    op.alter_column(
        "wallet_transactions",
        "updated_at",
        existing_type=sa.DateTime(),
        server_default=sa.text("now()"),
        type_=postgresql.TIMESTAMP(timezone=True),
        existing_nullable=False,
    )
    op.alter_column(
        "wallet_transactions",
        "created_at",
        existing_type=postgresql.TIMESTAMP(timezone=True),
        server_default=sa.text("now()"),
        existing_nullable=False,
    )
    op.alter_column(
        "wallet_transactions",
        "balance_after",
        existing_type=sa.Numeric(precision=14, scale=2),
        type_=sa.NUMERIC(precision=10, scale=2),
        existing_nullable=False,
    )
    op.alter_column(
        "wallet_transactions",
        "balance_before",
        existing_type=sa.Numeric(precision=14, scale=2),
        type_=sa.NUMERIC(precision=10, scale=2),
        existing_nullable=False,
    )
    op.alter_column(
        "wallet_transactions",
        "amount",
        existing_type=sa.Numeric(precision=14, scale=2),
        type_=sa.NUMERIC(precision=10, scale=2),
        existing_nullable=False,
    )
    op.drop_column("wallet_transactions", "deleted_at")
    op.drop_index(op.f("ix_wallet_balances_updated_at"), table_name="wallet_balances")
    op.drop_index(op.f("ix_wallet_balances_deleted_at"), table_name="wallet_balances")
    op.drop_index(op.f("ix_wallet_balances_created_at"), table_name="wallet_balances")
    op.drop_index(op.f("ix_wallet_balances_company_id"), table_name="wallet_balances")
    op.create_index(
        op.f("ix_wallet_balances_company_id"), "wallet_balances", ["company_id"], unique=False
    )
    op.create_unique_constraint(
        op.f("wallet_balances_company_id_key"),
        "wallet_balances",
        ["company_id"],
        postgresql_nulls_not_distinct=False,
    )
    op.alter_column(
        "wallet_balances",
        "updated_at",
        existing_type=sa.DateTime(),
        server_default=sa.text("now()"),
        type_=postgresql.TIMESTAMP(timezone=True),
        existing_nullable=False,
    )
    op.alter_column(
        "wallet_balances",
        "created_at",
        existing_type=sa.DateTime(),
        server_default=sa.text("now()"),
        type_=postgresql.TIMESTAMP(timezone=True),
        existing_nullable=False,
    )
    op.alter_column(
        "wallet_balances",
        "auto_topup_amount",
        existing_type=sa.Numeric(precision=14, scale=2),
        type_=sa.NUMERIC(precision=10, scale=2),
        existing_nullable=True,
    )
    op.alter_column(
        "wallet_balances",
        "auto_topup_threshold",
        existing_type=sa.Numeric(precision=14, scale=2),
        type_=sa.NUMERIC(precision=10, scale=2),
        existing_nullable=True,
    )
    op.alter_column(
        "wallet_balances",
        "auto_topup_enabled",
        existing_type=sa.BOOLEAN(),
        server_default=sa.text("false"),
        existing_nullable=False,
    )
    op.alter_column(
        "wallet_balances",
        "credit_limit",
        existing_type=sa.Numeric(precision=14, scale=2),
        server_default=sa.text("0"),
        type_=sa.NUMERIC(precision=10, scale=2),
        existing_nullable=True,
    )
    op.alter_column(
        "wallet_balances",
        "currency",
        existing_type=sa.VARCHAR(length=8),
        server_default=sa.text("'KZT'::character varying"),
        existing_nullable=False,
    )
    op.alter_column(
        "wallet_balances",
        "balance",
        existing_type=sa.Numeric(precision=14, scale=2),
        server_default=sa.text("0"),
        type_=sa.NUMERIC(precision=10, scale=2),
        existing_nullable=False,
    )
    op.drop_column("wallet_balances", "deleted_at")
    op.drop_column("wallet_balances", "version_id")
    op.drop_constraint(op.f("fk__users__company_id__companies"), "users", type_="foreignkey")
    op.create_foreign_key(
        op.f("fk_users_company_id"),
        "users",
        "companies",
        ["company_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.drop_index("ix_users_username_email_lower", table_name="users")
    op.drop_index("ix_users_login_fields", table_name="users")
    op.drop_index(op.f("ix_users_deleted_at"), table_name="users")
    op.drop_index("ix_users_company_active", table_name="users")
    op.drop_index("ix_users_active_role", table_name="users")
    op.create_unique_constraint(
        op.f("uq_user_username"), "users", ["username"], postgresql_nulls_not_distinct=False
    )
    op.create_unique_constraint(
        op.f("uq_user_phone"), "users", ["phone"], postgresql_nulls_not_distinct=False
    )
    op.create_unique_constraint(
        op.f("uq_user_email"), "users", ["email"], postgresql_nulls_not_distinct=False
    )
    op.alter_column(
        "users",
        "created_at",
        existing_type=postgresql.TIMESTAMP(),
        server_default=None,
        existing_nullable=False,
    )
    op.alter_column(
        "users",
        "deleted_at",
        existing_type=sa.DateTime(),
        type_=postgresql.TIMESTAMP(timezone=True),
        existing_nullable=True,
    )
    op.alter_column(
        "users",
        "locked_at",
        existing_type=sa.DateTime(),
        type_=postgresql.TIMESTAMP(timezone=True),
        existing_nullable=True,
    )
    op.alter_column(
        "users",
        "failed_login_attempts",
        existing_type=sa.INTEGER(),
        server_default=None,
        existing_nullable=False,
    )
    op.alter_column(
        "users",
        "is_superuser",
        existing_type=sa.BOOLEAN(),
        server_default=None,
        existing_nullable=False,
    )
    op.alter_column(
        "users",
        "is_verified",
        existing_type=sa.BOOLEAN(),
        server_default=None,
        existing_nullable=False,
    )
    op.alter_column(
        "users",
        "is_active",
        existing_type=sa.BOOLEAN(),
        server_default=None,
        existing_nullable=False,
    )
    op.alter_column(
        "users",
        "hashed_password",
        existing_type=sa.VARCHAR(length=255),
        server_default=None,
        existing_nullable=False,
    )
    op.alter_column(
        "users",
        "username",
        existing_type=sa.String(length=50),
        type_=sa.VARCHAR(length=255),
        nullable=False,
    )
    op.drop_column("users", "version")
    op.drop_column("users", "modified_at")
    op.drop_constraint(
        op.f("fk__user_sessions__user_id__users"), "user_sessions", type_="foreignkey"
    )
    op.drop_index("ix_user_sessions_user_expires", table_name="user_sessions")
    op.drop_index(op.f("ix_user_sessions_updated_at"), table_name="user_sessions")
    op.drop_index(op.f("ix_user_sessions_deleted_at"), table_name="user_sessions")
    op.drop_index("ix_user_sessions_active_user", table_name="user_sessions")
    op.alter_column(
        "user_sessions",
        "created_at",
        existing_type=postgresql.TIMESTAMP(),
        server_default=None,
        existing_nullable=False,
    )
    op.alter_column(
        "user_sessions",
        "is_active",
        existing_type=sa.BOOLEAN(),
        server_default=None,
        existing_nullable=False,
    )
    op.drop_column("user_sessions", "deleted_at")
    op.drop_column("user_sessions", "terminated_at")
    op.drop_index(op.f("ix_subscriptions_updated_at"), table_name="subscriptions")
    op.drop_index(op.f("ix_subscriptions_deleted_at"), table_name="subscriptions")
    op.drop_index(op.f("ix_subscriptions_created_at"), table_name="subscriptions")
    op.drop_index("ix_subscription_company_status", table_name="subscriptions")
    op.alter_column(
        "subscriptions",
        "updated_at",
        existing_type=sa.DateTime(),
        server_default=sa.text("now()"),
        type_=postgresql.TIMESTAMP(timezone=True),
        existing_nullable=False,
    )
    op.alter_column(
        "subscriptions",
        "created_at",
        existing_type=sa.DateTime(),
        server_default=sa.text("now()"),
        type_=postgresql.TIMESTAMP(timezone=True),
        existing_nullable=False,
    )
    op.alter_column(
        "subscriptions",
        "trial_used",
        existing_type=sa.BOOLEAN(),
        server_default=sa.text("false"),
        existing_nullable=False,
    )
    op.alter_column(
        "subscriptions",
        "auto_renew",
        existing_type=sa.BOOLEAN(),
        server_default=sa.text("true"),
        existing_nullable=False,
    )
    op.alter_column(
        "subscriptions",
        "currency",
        existing_type=sa.VARCHAR(length=8),
        server_default=sa.text("'KZT'::character varying"),
        existing_nullable=False,
    )
    op.alter_column(
        "subscriptions",
        "price",
        existing_type=sa.Numeric(precision=14, scale=2),
        type_=sa.NUMERIC(precision=10, scale=2),
        existing_nullable=False,
    )
    op.alter_column(
        "subscriptions",
        "billing_cycle",
        existing_type=sa.VARCHAR(length=32),
        server_default=sa.text("'monthly'::character varying"),
        existing_nullable=False,
    )
    op.drop_column("subscriptions", "deleted_at")
    op.drop_column("subscriptions", "grace_period_days")
    op.drop_constraint(
        op.f("fk__stock_movements__product_id__products"), "stock_movements", type_="foreignkey"
    )
    op.drop_constraint(
        op.f("fk__stock_movements__order_id__orders"), "stock_movements", type_="foreignkey"
    )
    op.drop_index(op.f("ix_stock_movements_product_id"), table_name="stock_movements")
    op.drop_index(op.f("ix_stock_movements_order_id"), table_name="stock_movements")
    op.drop_index(op.f("ix_stock_movements_is_archived"), table_name="stock_movements")
    op.drop_index(op.f("ix_stock_movements_id"), table_name="stock_movements")
    op.drop_index("ix_movements_stock_created", table_name="stock_movements")
    op.drop_index("ix_movements_product_type", table_name="stock_movements")
    op.drop_index("ix_movements_order", table_name="stock_movements")
    op.alter_column(
        "stock_movements",
        "updated_at",
        existing_type=sa.DateTime(),
        server_default=sa.text("now()"),
        type_=postgresql.TIMESTAMP(timezone=True),
        existing_nullable=False,
    )
    op.alter_column(
        "stock_movements",
        "created_at",
        existing_type=sa.DateTime(),
        server_default=sa.text("now()"),
        type_=postgresql.TIMESTAMP(timezone=True),
        existing_nullable=False,
    )
    op.alter_column("stock_movements", "stock_id", existing_type=sa.INTEGER(), nullable=False)
    op.drop_column("stock_movements", "archived_at")
    op.drop_column("stock_movements", "is_archived")
    op.drop_column("stock_movements", "order_id")
    op.drop_column("stock_movements", "product_id")
    (
        op.add_column(
            "products",
            sa.Column(
                "height", sa.NUMERIC(precision=8, scale=2), autoincrement=False, nullable=True
            ),
        ),
    )
    (
        op.add_column(
            "products",
            sa.Column(
                "is_digital",
                sa.BOOLEAN(),
                server_default=sa.text("false"),
                autoincrement=False,
                nullable=False,
            ),
        ),
    )
    (
        op.add_column(
            "products",
            sa.Column(
                "weight", sa.NUMERIC(precision=8, scale=3), autoincrement=False, nullable=True
            ),
        ),
    )
    (
        op.add_column(
            "products",
            sa.Column(
                "length", sa.NUMERIC(precision=8, scale=2), autoincrement=False, nullable=True
            ),
        ),
    )
    (
        op.add_column(
            "products",
            sa.Column(
                "width", sa.NUMERIC(precision=8, scale=2), autoincrement=False, nullable=True
            ),
        ),
    )
    op.drop_constraint(
        op.f("fk__products__category_id__categories"), "products", type_="foreignkey"
    )
    op.drop_index(op.f("ix_products_repriced_at"), table_name="products")
    op.drop_index(op.f("ix_products_price_updated_at"), table_name="products")
    op.drop_index(op.f("ix_products_price"), table_name="products")
    op.drop_index(op.f("ix_products_kaspi_status"), table_name="products")
    op.drop_index(op.f("ix_products_is_featured"), table_name="products")
    op.drop_index(op.f("ix_products_deleted_at"), table_name="products")
    op.drop_index("ix_product_search_name_sku", table_name="products")
    op.drop_index("ix_product_search_name", table_name="products")
    op.drop_index("ix_product_company_featured", table_name="products")
    op.drop_index("ix_product_company_category_active_del", table_name="products")
    op.drop_index("ix_product_company_active", table_name="products")
    op.drop_index("ix_kaspi_company_status", table_name="products")
    op.drop_index(op.f("ix_products_slug"), table_name="products")
    op.create_index(op.f("ix_products_slug"), "products", ["slug"], unique=True)
    op.drop_index(op.f("ix_products_sku"), table_name="products")
    op.create_index(op.f("ix_products_sku"), "products", ["sku"], unique=True)
    op.drop_index("ix_product_price_active", table_name="products")
    op.create_index(
        op.f("ix_product_price_active"), "products", ["price", "is_active"], unique=False
    )
    op.create_index(op.f("ix_product_stock"), "products", ["stock_quantity"], unique=False)
    op.create_index(
        op.f("ix_product_category_active"), "products", ["category_id", "is_active"], unique=False
    )
    op.alter_column(
        "products",
        "version",
        existing_type=sa.INTEGER(),
        server_default=sa.text("1"),
        existing_nullable=False,
    )
    op.alter_column(
        "products",
        "updated_at",
        existing_type=postgresql.TIMESTAMP(),
        server_default=sa.text("now()"),
        existing_nullable=False,
    )
    op.alter_column(
        "products",
        "deleted_at",
        existing_type=sa.DateTime(),
        type_=postgresql.TIMESTAMP(timezone=True),
        existing_nullable=True,
    )
    op.alter_column(
        "products",
        "extra",
        existing_type=sa.Text(),
        server_default=sa.text("'{}'::jsonb"),
        type_=postgresql.JSONB(astext_type=sa.Text()),
        nullable=False,
    )
    op.alter_column(
        "products",
        "price_updated_at",
        existing_type=sa.DateTime(),
        type_=postgresql.TIMESTAMP(timezone=True),
        existing_nullable=True,
    )
    op.alter_column(
        "products",
        "repriced_at",
        existing_type=sa.DateTime(),
        type_=postgresql.TIMESTAMP(timezone=True),
        existing_nullable=True,
    )
    op.alter_column(
        "products",
        "preorder_show_zero_stock",
        existing_type=sa.BOOLEAN(),
        server_default=sa.text("false"),
        existing_nullable=False,
    )
    op.alter_column(
        "products",
        "preorder_lead_days",
        existing_type=sa.INTEGER(),
        server_default=sa.text("0"),
        nullable=False,
    )
    op.alter_column(
        "products",
        "is_active",
        existing_type=sa.BOOLEAN(),
        server_default=None,
        existing_nullable=False,
    )
    op.alter_column(
        "products",
        "stock_quantity",
        existing_type=sa.INTEGER(),
        server_default=None,
        existing_nullable=False,
    )
    op.alter_column(
        "products",
        "sale_price",
        existing_type=sa.Numeric(precision=14, scale=2),
        type_=sa.NUMERIC(precision=10, scale=2),
        existing_nullable=True,
    )
    op.alter_column(
        "products",
        "cost_price",
        existing_type=sa.Numeric(precision=14, scale=2),
        type_=sa.NUMERIC(precision=10, scale=2),
        existing_nullable=True,
    )
    op.alter_column(
        "products",
        "price",
        existing_type=sa.Numeric(precision=14, scale=2),
        type_=sa.NUMERIC(precision=10, scale=2),
        nullable=False,
    )
    op.alter_column("products", "sku", existing_type=sa.VARCHAR(length=100), nullable=False)
    op.alter_column("products", "slug", existing_type=sa.VARCHAR(length=255), nullable=False)
    op.alter_column("products", "name", existing_type=sa.VARCHAR(length=255), nullable=False)
    op.add_column(
        "product_variants",
        sa.Column("variant_name", sa.VARCHAR(length=255), autoincrement=False, nullable=True),
    )
    op.add_column(
        "product_variants", sa.Column("stock", sa.INTEGER(), autoincrement=False, nullable=True)
    )
    op.drop_constraint("uq_variant_product_sku", "product_variants", type_="unique")
    op.drop_index(op.f("ix_product_variants_name"), table_name="product_variants")
    op.drop_index(op.f("ix_product_variants_is_active"), table_name="product_variants")
    op.drop_index(op.f("ix_product_variants_deleted_at"), table_name="product_variants")
    op.drop_index(op.f("ix_product_variants_sku"), table_name="product_variants")
    op.create_index(op.f("ix_product_variants_sku"), "product_variants", ["sku"], unique=True)
    op.create_unique_constraint(
        op.f("uq_variant_sku"), "product_variants", ["sku"], postgresql_nulls_not_distinct=False
    )
    op.create_index(
        op.f("ix_product_variants_variant_name"), "product_variants", ["variant_name"], unique=False
    )
    op.alter_column(
        "product_variants",
        "created_at",
        existing_type=postgresql.TIMESTAMP(),
        server_default=None,
        existing_nullable=False,
    )
    op.alter_column(
        "product_variants",
        "is_active",
        existing_type=sa.BOOLEAN(),
        server_default=None,
        existing_nullable=False,
    )
    op.alter_column(
        "product_variants",
        "stock_quantity",
        existing_type=sa.INTEGER(),
        server_default=None,
        existing_nullable=False,
    )
    op.alter_column(
        "product_variants",
        "sale_price",
        existing_type=sa.Numeric(precision=14, scale=2),
        type_=sa.NUMERIC(precision=10, scale=2),
        existing_nullable=True,
    )
    op.alter_column(
        "product_variants",
        "cost_price",
        existing_type=sa.Numeric(precision=14, scale=2),
        type_=sa.NUMERIC(precision=10, scale=2),
        existing_nullable=True,
    )
    op.alter_column(
        "product_variants",
        "price",
        existing_type=sa.Numeric(precision=14, scale=2),
        type_=sa.NUMERIC(precision=10, scale=2),
        existing_nullable=True,
    )
    op.alter_column("product_variants", "sku", existing_type=sa.VARCHAR(length=100), nullable=False)
    op.drop_column("product_variants", "version")
    op.drop_column("product_variants", "deleted_at")
    op.drop_index("ix_stock_product_warehouse_qty", table_name="product_stocks")
    op.drop_index("ix_stock_low", table_name="product_stocks")
    op.drop_index(op.f("ix_product_stocks_updated_at"), table_name="product_stocks")
    op.drop_index(op.f("ix_product_stocks_is_archived"), table_name="product_stocks")
    op.drop_index(op.f("ix_product_stocks_id"), table_name="product_stocks")
    op.drop_index(op.f("ix_product_stocks_created_at"), table_name="product_stocks")
    op.alter_column(
        "product_stocks",
        "updated_at",
        existing_type=sa.DateTime(),
        server_default=sa.text("now()"),
        type_=postgresql.TIMESTAMP(timezone=True),
        existing_nullable=False,
    )
    op.alter_column(
        "product_stocks",
        "created_at",
        existing_type=sa.DateTime(),
        server_default=sa.text("now()"),
        type_=postgresql.TIMESTAMP(timezone=True),
        existing_nullable=False,
    )
    op.alter_column(
        "product_stocks",
        "min_quantity",
        existing_type=sa.INTEGER(),
        server_default=sa.text("0"),
        existing_nullable=False,
    )
    op.alter_column(
        "product_stocks",
        "reserved_quantity",
        existing_type=sa.INTEGER(),
        server_default=sa.text("0"),
        existing_nullable=False,
    )
    op.alter_column(
        "product_stocks",
        "quantity",
        existing_type=sa.INTEGER(),
        server_default=sa.text("0"),
        existing_nullable=False,
    )
    op.drop_column("product_stocks", "archived_at")
    op.drop_column("product_stocks", "is_archived")
    op.drop_column("product_stocks", "last_restocked_at")
    op.drop_column("product_stocks", "cost_price")
    op.add_column(
        "payments",
        sa.Column(
            "refund_amount",
            sa.NUMERIC(precision=18, scale=6),
            server_default=sa.text("'0'::numeric"),
            autoincrement=False,
            nullable=False,
        ),
    )
    op.add_column(
        "payments",
        sa.Column("wallet_account_id", sa.INTEGER(), autoincrement=False, nullable=False),
    )
    op.add_column("payments", sa.Column("reference", sa.TEXT(), autoincrement=False, nullable=True))
    op.add_column(
        "payments", sa.Column("user_id", sa.INTEGER(), autoincrement=False, nullable=False)
    )
    op.drop_constraint(op.f("fk__payments__order_id__orders"), "payments", type_="foreignkey")
    op.drop_index(op.f("ix_payments_uuid"), table_name="payments")
    op.drop_index(op.f("ix_payments_updated_at"), table_name="payments")
    op.drop_index("ix_payments_refund_hist_gin", table_name="payments", postgresql_using="gin")
    op.drop_index("ix_payments_provider_status", table_name="payments")
    op.drop_index(op.f("ix_payments_provider_invoice_id"), table_name="payments")
    op.drop_index("ix_payments_provider_data_gin", table_name="payments", postgresql_using="gin")
    op.drop_index(op.f("ix_payments_payment_number"), table_name="payments")
    op.drop_index("ix_payments_order_status", table_name="payments")
    op.drop_index(op.f("ix_payments_order_id"), table_name="payments")
    op.drop_index(op.f("ix_payments_id"), table_name="payments")
    op.drop_index(op.f("ix_payments_external_id"), table_name="payments")
    op.drop_index(op.f("ix_payments_customer_ip"), table_name="payments")
    op.drop_index(op.f("ix_payments_customer_id"), table_name="payments")
    op.drop_index(op.f("ix_payments_created_at"), table_name="payments")
    op.create_index(
        op.f("ix_payments_wallet_account_id"), "payments", ["wallet_account_id"], unique=False
    )
    op.create_index(op.f("ix_payments_user_id"), "payments", ["user_id"], unique=False)
    op.create_index(op.f("ix_payments_currency"), "payments", ["currency"], unique=False)
    op.alter_column(
        "payments",
        "currency",
        existing_type=sa.String(length=8),
        type_=sa.VARCHAR(length=10),
        existing_nullable=False,
    )
    op.alter_column(
        "payments",
        "amount",
        existing_type=sa.Numeric(precision=14, scale=2),
        type_=sa.NUMERIC(precision=18, scale=6),
        existing_nullable=False,
    )
    op.alter_column(
        "payments",
        "status",
        existing_type=paymentstatus_enum,
        type_=sa.VARCHAR(length=20),
        existing_nullable=False,
    )
    op.alter_column(
        "payments",
        "updated_at",
        existing_type=sa.DateTime(),
        server_default=None,
        type_=sa.VARCHAR(length=40),
        existing_nullable=False,
    )
    op.alter_column(
        "payments",
        "created_at",
        existing_type=sa.DateTime(),
        server_default=None,
        type_=sa.VARCHAR(length=40),
        existing_nullable=False,
    )
    op.drop_column("payments", "is_test")
    op.drop_column("payments", "failure_code")
    op.drop_column("payments", "failure_reason")
    op.drop_column("payments", "user_agent")
    op.drop_column("payments", "customer_ip")
    op.drop_column("payments", "description")
    op.drop_column("payments", "cancelled_at")
    op.drop_column("payments", "failed_at")
    op.drop_column("payments", "confirmed_at")
    op.drop_column("payments", "processed_at")
    op.drop_column("payments", "receipt_number")
    op.drop_column("payments", "receipt_url")
    op.drop_column("payments", "provider_data")
    op.drop_column("payments", "refund_reason_history")
    op.drop_column("payments", "refunded_amount")
    op.drop_column("payments", "fee_amount")
    op.drop_column("payments", "method")
    op.drop_column("payments", "provider")
    op.drop_column("payments", "provider_invoice_id")
    op.drop_column("payments", "external_id")
    op.drop_column("payments", "payment_number")
    op.drop_column("payments", "order_id")
    op.drop_column("payments", "version")
    op.drop_column("payments", "uuid")
    op.drop_index(op.f("ix_payment_refunds_uuid"), table_name="payment_refunds")
    op.drop_index(op.f("ix_payment_refunds_updated_at"), table_name="payment_refunds")
    op.drop_index(
        "ix_payment_refunds_provider_data_gin", table_name="payment_refunds", postgresql_using="gin"
    )
    op.drop_index("ix_payment_refunds_payment_status", table_name="payment_refunds")
    op.drop_index(op.f("ix_payment_refunds_id"), table_name="payment_refunds")
    op.drop_index(op.f("ix_payment_refunds_created_at"), table_name="payment_refunds")
    op.alter_column(
        "payment_refunds",
        "provider_data",
        existing_type=postgresql.JSONB(astext_type=sa.Text()),
        type_=sa.TEXT(),
        existing_nullable=True,
    )
    op.alter_column(
        "payment_refunds",
        "completed_at",
        existing_type=sa.DateTime(),
        type_=postgresql.TIMESTAMP(timezone=True),
        existing_nullable=True,
    )
    op.alter_column(
        "payment_refunds",
        "processed_at",
        existing_type=sa.DateTime(),
        type_=postgresql.TIMESTAMP(timezone=True),
        existing_nullable=True,
    )
    op.alter_column(
        "payment_refunds",
        "currency",
        existing_type=sa.VARCHAR(length=8),
        server_default=sa.text("'KZT'::character varying"),
        existing_nullable=False,
    )
    op.alter_column(
        "payment_refunds",
        "updated_at",
        existing_type=sa.DateTime(),
        type_=postgresql.TIMESTAMP(timezone=True),
        existing_nullable=False,
        existing_server_default=sa.text("now()"),
    )
    op.alter_column(
        "payment_refunds",
        "created_at",
        existing_type=sa.DateTime(),
        type_=postgresql.TIMESTAMP(timezone=True),
        existing_nullable=False,
        existing_server_default=sa.text("now()"),
    )
    op.drop_column("payment_refunds", "failed_at")
    op.drop_column("payment_refunds", "version")
    op.drop_column("payment_refunds", "uuid")
    op.drop_constraint(op.f("fk__otp_codes__user_id__users"), "otp_codes", type_="foreignkey")
    op.drop_constraint("uq_otp_code_phone_purpose", "otp_codes", type_="unique")
    op.drop_index("ix_otpcode_purpose", table_name="otp_codes")
    op.drop_index("ix_otpcode_phone_purpose_created", table_name="otp_codes")
    op.drop_index("ix_otpcode_phone", table_name="otp_codes")
    op.drop_index("ix_otpcode_is_used", table_name="otp_codes")
    op.drop_index("ix_otpcode_expires_at", table_name="otp_codes")
    op.drop_index("ix_otpcode_created_at", table_name="otp_codes")
    op.create_index(op.f("ix_otp_codes_phone"), "otp_codes", ["phone"], unique=False)
    op.alter_column(
        "otp_codes",
        "purpose",
        existing_type=sa.String(length=32),
        type_=sa.VARCHAR(length=50),
        existing_nullable=False,
    )
    op.alter_column(
        "otp_codes",
        "code",
        existing_type=sa.String(length=16),
        type_=sa.VARCHAR(length=6),
        existing_nullable=False,
    )
    op.alter_column(
        "otp_codes",
        "phone",
        existing_type=sa.String(length=32),
        type_=sa.VARCHAR(length=20),
        existing_nullable=False,
    )
    op.drop_column("otp_codes", "verified_at")
    op.drop_column("otp_codes", "user_id")
    op.drop_constraint("uq_orders_order_number", "orders", type_="unique")
    op.drop_index(op.f("ix_orders_updated_at"), table_name="orders")
    op.drop_index("ix_orders_source_created", table_name="orders")
    op.drop_index(op.f("ix_orders_created_at"), table_name="orders")
    op.drop_index("ix_orders_company_status", table_name="orders")
    op.alter_column(
        "orders",
        "currency",
        existing_type=sa.VARCHAR(length=8),
        server_default=sa.text("'KZT'::character varying"),
        existing_nullable=False,
    )
    op.alter_column(
        "orders",
        "discount_amount",
        existing_type=sa.NUMERIC(precision=14, scale=2),
        server_default=sa.text("0"),
        existing_nullable=False,
    )
    op.alter_column(
        "orders",
        "shipping_amount",
        existing_type=sa.NUMERIC(precision=14, scale=2),
        server_default=sa.text("0"),
        existing_nullable=False,
    )
    op.alter_column(
        "orders",
        "tax_amount",
        existing_type=sa.NUMERIC(precision=14, scale=2),
        server_default=sa.text("0"),
        existing_nullable=False,
    )
    op.alter_column(
        "orders",
        "updated_at",
        existing_type=sa.DateTime(),
        server_default=sa.text("now()"),
        type_=postgresql.TIMESTAMP(timezone=True),
        existing_nullable=False,
    )
    op.alter_column(
        "orders",
        "created_at",
        existing_type=sa.DateTime(),
        server_default=sa.text("now()"),
        type_=postgresql.TIMESTAMP(timezone=True),
        existing_nullable=False,
    )
    op.add_column(
        "order_items",
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            autoincrement=False,
            nullable=False,
        ),
    )
    op.add_column(
        "order_items",
        sa.Column(
            "updated_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            autoincrement=False,
            nullable=False,
        ),
    )
    op.drop_index(op.f("ix_order_items_sku"), table_name="order_items")
    op.drop_index("ix_order_items_order_sku", table_name="order_items")
    op.drop_column("order_items", "cost_price")
    op.add_column(
        "messages",
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(),
            server_default=sa.text("now()"),
            autoincrement=False,
            nullable=False,
        ),
    )
    op.add_column(
        "messages",
        sa.Column(
            "updated_at",
            postgresql.TIMESTAMP(),
            server_default=sa.text("now()"),
            autoincrement=False,
            nullable=False,
        ),
    )
    op.drop_constraint(op.f("fk__messages__campaign_id__campaigns"), "messages", type_="foreignkey")
    op.create_foreign_key(
        op.f("fk_messages_campaign_id_campaigns"), "messages", "campaigns", ["campaign_id"], ["id"]
    )
    op.drop_index(op.f("ix_messages_recipient"), table_name="messages")
    op.drop_index(op.f("ix_messages_provider_message_id"), table_name="messages")
    op.drop_index(op.f("ix_messages_error_code"), table_name="messages")
    op.drop_index(op.f("ix_messages_deleted_by"), table_name="messages")
    op.drop_index(op.f("ix_messages_deleted_at"), table_name="messages")
    op.drop_index(op.f("ix_messages_channel"), table_name="messages")
    op.drop_index("ix_message_campaign_status_channel", table_name="messages")
    op.alter_column(
        "messages",
        "sent_at",
        existing_type=sa.DateTime(timezone=True),
        type_=postgresql.TIMESTAMP(),
        existing_nullable=True,
    )
    op.alter_column(
        "messages",
        "status",
        existing_type=message_status_enum,
        server_default=sa.text("'PENDING'::messagestatus"),
        type_=postgresql.ENUM("PENDING", "SENT", "DELIVERED", "FAILED", name="messagestatus"),
        existing_nullable=False,
    )
    op.drop_column("messages", "delete_reason")
    op.drop_column("messages", "deleted_by")
    op.drop_column("messages", "deleted_at")
    op.drop_column("messages", "error_code")
    op.drop_column("messages", "delivered_at")
    op.drop_column("messages", "provider_message_id")
    op.drop_column("messages", "channel")
    op.drop_constraint(op.f("fk__invoices__order_id__orders"), "invoices", type_="foreignkey")
    op.drop_index(op.f("ix_invoices_updated_at"), table_name="invoices")
    op.drop_index(op.f("ix_invoices_deleted_at"), table_name="invoices")
    op.drop_index(op.f("ix_invoices_created_at"), table_name="invoices")
    op.drop_index("ix_invoice_company_status", table_name="invoices")
    op.drop_index("ix_invoice_company_created", table_name="invoices")
    op.drop_index(op.f("ix_invoices_invoice_number"), table_name="invoices")
    op.create_index(
        op.f("ix_invoices_invoice_number"), "invoices", ["invoice_number"], unique=False
    )
    op.create_unique_constraint(
        op.f("invoices_invoice_number_key"),
        "invoices",
        ["invoice_number"],
        postgresql_nulls_not_distinct=False,
    )
    op.alter_column(
        "invoices",
        "updated_at",
        existing_type=sa.DateTime(),
        server_default=sa.text("now()"),
        type_=postgresql.TIMESTAMP(timezone=True),
        existing_nullable=False,
    )
    op.alter_column(
        "invoices",
        "created_at",
        existing_type=sa.DateTime(),
        server_default=sa.text("now()"),
        type_=postgresql.TIMESTAMP(timezone=True),
        existing_nullable=False,
    )
    op.alter_column(
        "invoices",
        "currency",
        existing_type=sa.VARCHAR(length=8),
        server_default=sa.text("'KZT'::character varying"),
        existing_nullable=False,
    )
    op.alter_column(
        "invoices",
        "total_amount",
        existing_type=sa.Numeric(precision=14, scale=2),
        type_=sa.NUMERIC(precision=10, scale=2),
        existing_nullable=False,
    )
    op.alter_column(
        "invoices",
        "tax_amount",
        existing_type=sa.Numeric(precision=14, scale=2),
        server_default=sa.text("0"),
        type_=sa.NUMERIC(precision=10, scale=2),
        nullable=True,
    )
    op.alter_column(
        "invoices",
        "subtotal",
        existing_type=sa.Numeric(precision=14, scale=2),
        type_=sa.NUMERIC(precision=10, scale=2),
        existing_nullable=False,
    )
    op.drop_column("invoices", "deleted_at")
    op.drop_index(op.f("ix_inventory_outbox_updated_at"), table_name="inventory_outbox")
    op.drop_index(op.f("ix_inventory_outbox_status"), table_name="inventory_outbox")
    op.drop_index(op.f("ix_inventory_outbox_processed_at"), table_name="inventory_outbox")
    op.drop_index(op.f("ix_inventory_outbox_next_attempt_at"), table_name="inventory_outbox")
    op.drop_index(op.f("ix_inventory_outbox_id"), table_name="inventory_outbox")
    op.drop_index(op.f("ix_inventory_outbox_event_type"), table_name="inventory_outbox")
    op.drop_index(op.f("ix_inventory_outbox_deleted_at"), table_name="inventory_outbox")
    op.drop_index(op.f("ix_inventory_outbox_created_at"), table_name="inventory_outbox")
    op.drop_index(op.f("ix_inventory_outbox_channel"), table_name="inventory_outbox")
    op.drop_index(op.f("ix_inventory_outbox_aggregate_type"), table_name="inventory_outbox")
    op.drop_index(op.f("ix_inventory_outbox_aggregate_id"), table_name="inventory_outbox")
    op.drop_index("ix_inv_outbox_status_due", table_name="inventory_outbox")
    op.drop_index("ix_inv_outbox_aggregate_created", table_name="inventory_outbox")
    op.create_index(op.f("ix_outbox_status"), "inventory_outbox", ["status"], unique=False)
    op.create_index(op.f("ix_outbox_event"), "inventory_outbox", ["event_type"], unique=False)
    op.create_index(
        op.f("ix_outbox_aggregate"),
        "inventory_outbox",
        ["aggregate_type", "aggregate_id"],
        unique=False,
    )
    op.alter_column(
        "inventory_outbox",
        "updated_at",
        existing_type=postgresql.TIMESTAMP(),
        server_default=sa.text("now()"),
        existing_nullable=False,
    )
    op.alter_column(
        "inventory_outbox",
        "created_at",
        existing_type=postgresql.TIMESTAMP(),
        server_default=sa.text("now()"),
        existing_nullable=False,
    )
    op.alter_column(
        "inventory_outbox",
        "attempts",
        existing_type=sa.INTEGER(),
        server_default=sa.text("0"),
        existing_nullable=False,
    )
    op.alter_column(
        "inventory_outbox",
        "status",
        existing_type=sa.String(length=16),
        server_default=sa.text("'pending'::character varying"),
        type_=sa.VARCHAR(length=32),
        existing_nullable=False,
    )
    op.alter_column(
        "inventory_outbox",
        "channel",
        existing_type=sa.VARCHAR(length=64),
        server_default=sa.text("'erp'::character varying"),
        nullable=False,
    )
    op.alter_column(
        "inventory_outbox",
        "payload",
        existing_type=postgresql.JSONB(astext_type=sa.Text()),
        type_=sa.TEXT(),
        nullable=False,
    )
    op.drop_index(op.f("ix_customers_tenant_id"), table_name="customers")
    op.drop_index("ix_customers_phone", table_name="customers")
    op.drop_index(op.f("ix_customers_last_modified_by"), table_name="customers")
    op.drop_index(op.f("ix_customers_id"), table_name="customers")
    op.drop_index(op.f("ix_customers_deleted_at"), table_name="customers")
    op.drop_index("ix_customers_active_email", table_name="customers")
    op.alter_column(
        "customers",
        "updated_at",
        existing_type=postgresql.TIMESTAMP(),
        server_default=sa.text("now()"),
        existing_nullable=False,
    )
    op.alter_column(
        "customers",
        "is_active",
        existing_type=sa.BOOLEAN(),
        server_default=sa.text("true"),
        existing_nullable=False,
    )
    op.drop_column("customers", "last_modified_by")
    op.drop_column("customers", "deleted_at")
    op.drop_column("customers", "tenant_id")
    op.drop_constraint(op.f("fk__companies__owner_id__users"), "companies", type_="foreignkey")
    op.drop_index("ix_company_active_plan", table_name="companies")
    op.drop_index(op.f("ix_companies_updated_at"), table_name="companies")
    op.drop_index(op.f("ix_companies_synced_at"), table_name="companies")
    op.drop_index(op.f("ix_companies_sync_source"), table_name="companies")
    op.drop_index(op.f("ix_companies_onec_id"), table_name="companies")
    op.drop_index(op.f("ix_companies_last_sync_error_code"), table_name="companies")
    op.drop_index(op.f("ix_companies_id"), table_name="companies")
    op.drop_index(op.f("ix_companies_gdpr_consent_version"), table_name="companies")
    op.drop_index(op.f("ix_companies_gdpr_consent_at"), table_name="companies")
    op.drop_index(op.f("ix_companies_external_id"), table_name="companies")
    op.drop_index(op.f("ix_companies_deleted_by"), table_name="companies")
    op.drop_index(op.f("ix_companies_deleted_at"), table_name="companies")
    op.drop_index(op.f("ix_companies_created_at"), table_name="companies")
    op.alter_column(
        "companies",
        "updated_at",
        existing_type=sa.DateTime(timezone=True),
        server_default=sa.text("now()"),
        type_=postgresql.TIMESTAMP(),
        existing_nullable=False,
    )
    op.alter_column(
        "companies",
        "created_at",
        existing_type=sa.DateTime(timezone=True),
        server_default=sa.text("now()"),
        type_=postgresql.TIMESTAMP(),
        existing_nullable=False,
    )
    op.alter_column(
        "companies",
        "subscription_expires_at",
        existing_type=postgresql.TIMESTAMP(
            timezone=True
        ),  #         РµСЃР»Рё СЂР°РЅСЊС€Рµ Р±С‹Р» timestamptz; РёРЅР°С‡Рµ РїРѕСЃС‚Р°РІСЊ postgresql.TIMESTAMP()
        type_=sa.DateTime(timezone=True),
        existing_nullable=True,
        postgresql_using="(subscription_expires_at::timestamp AT TIME ZONE 'Asia/Almaty')",
    )

    op.alter_column(
        "companies",
        "subscription_plan",
        existing_type=sa.VARCHAR(length=32),
        server_default=sa.text("'start'::character varying"),
        existing_nullable=False,
    )
    op.alter_column(
        "companies",
        "is_active",
        existing_type=sa.BOOLEAN(),
        server_default=sa.text("true"),
        existing_nullable=False,
    )
    op.drop_column("companies", "gdpr_consent_ip")
    op.drop_column("companies", "gdpr_consent_version")
    op.drop_column("companies", "gdpr_consent_at")
    op.drop_column("companies", "delete_reason")
    op.drop_column("companies", "deleted_by")
    op.drop_column("companies", "settings_history")
    op.drop_column("companies", "settings_version")
    op.drop_column("companies", "last_sync_error_message")
    op.drop_column("companies", "last_sync_error_code")
    op.drop_column("companies", "synced_at")
    op.drop_column("companies", "sync_source")
    op.drop_column("companies", "onec_id")
    op.drop_column("companies", "external_id")
    op.drop_constraint(
        op.f("fk__categories__parent_id__categories"), "categories", type_="foreignkey"
    )
    op.drop_index(op.f("ix_categories_deleted_at"), table_name="categories")
    op.create_unique_constraint(
        op.f("uq_category_slug"), "categories", ["slug"], postgresql_nulls_not_distinct=False
    )
    op.alter_column(
        "categories",
        "created_at",
        existing_type=postgresql.TIMESTAMP(),
        server_default=None,
        existing_nullable=False,
    )
    op.alter_column(
        "categories",
        "sort_order",
        existing_type=sa.INTEGER(),
        server_default=None,
        existing_nullable=False,
    )
    op.alter_column(
        "categories",
        "is_active",
        existing_type=sa.BOOLEAN(),
        server_default=None,
        existing_nullable=False,
    )
    op.drop_column("categories", "deleted_at")
    op.add_column(
        "campaigns",
        sa.Column("created_at", sa.VARCHAR(length=40), autoincrement=False, nullable=True),
    )
    op.add_column(
        "campaigns",
        sa.Column(
            "archived",
            sa.BOOLEAN(),
            server_default=sa.text("false"),
            autoincrement=False,
            nullable=False,
        ),
    )
    op.add_column(
        "campaigns", sa.Column("owner", sa.VARCHAR(length=100), autoincrement=False, nullable=True)
    )
    op.add_column("campaigns", sa.Column("tags", sa.TEXT(), autoincrement=False, nullable=True))
    op.add_column(
        "campaigns",
        sa.Column("schedule", sa.VARCHAR(length=40), autoincrement=False, nullable=True),
    )
    op.add_column(
        "campaigns",
        sa.Column("updated_at", sa.VARCHAR(length=40), autoincrement=False, nullable=True),
    )
    op.add_column(
        "campaigns",
        sa.Column(
            "active",
            sa.BOOLEAN(),
            server_default=sa.text("true"),
            autoincrement=False,
            nullable=False,
        ),
    )
    op.drop_constraint(
        op.f("fk__campaigns__company_id__companies"), "campaigns", type_="foreignkey"
    )
    op.drop_constraint("uq_campaign_company_title_scheduled", "campaigns", type_="unique")
    op.drop_index(op.f("ix_campaigns_scheduled_at"), table_name="campaigns")
    op.drop_index(op.f("ix_campaigns_deleted_by"), table_name="campaigns")
    op.drop_index(op.f("ix_campaigns_deleted_at"), table_name="campaigns")
    op.drop_index(op.f("ix_campaigns_company_id"), table_name="campaigns")
    op.drop_index("ix_campaign_scheduled_at_status", table_name="campaigns")
    op.drop_index("ix_campaign_company_status", table_name="campaigns")
    op.create_unique_constraint(
        op.f("uq_campaign_title"), "campaigns", ["title"], postgresql_nulls_not_distinct=False
    )
    op.create_index(
        op.f("ix_campaigns_title_lower"),
        "campaigns",
        [sa.literal_column("lower(title::text)")],
        unique=False,
    )
    op.create_index(op.f("ix_campaigns_status"), "campaigns", ["status"], unique=False)
    op.create_index(
        op.f("ix_campaigns_active_archived"), "campaigns", ["active", "archived"], unique=False
    )
    op.alter_column(
        "campaigns",
        "scheduled_at",
        existing_type=postgresql.TIMESTAMP(),  # РµСЃР»Рё СЂР°РЅСЊС€Рµ Р±С‹Р» Р±РµР· С‚Р°Р№РјР·РѕРЅС‹
        type_=sa.DateTime(timezone=True),
        existing_nullable=True,
        postgresql_using="(scheduled_at AT TIME ZONE 'Asia/Almaty')",
    )

    op.execute("ALTER TABLE campaigns ALTER COLUMN status DROP DEFAULT")

    # 2) РЅРѕСЂРјР°Р»РёР·СѓРµРј РґР°РЅРЅС‹Рµ (РµСЃР»Рё РІ С‚Р°Р±Р»РёС†Рµ РІРґСЂСѓРі РµСЃС‚СЊ РЅРµРѕР¶РёРґР°РЅРЅС‹Рµ СЃС‚СЂРѕРєРё/NULL)
    op.execute(
        """
    UPDATE campaigns
    SET status = 'DRAFT'
    WHERE status IS NULL
    OR status::text NOT IN ('DRAFT','ACTIVE','PAUSED','COMPLETED');
    """
    )

    # 3) РјРµРЅСЏРµРј С‚РёРї РєРѕР»РѕРЅРєРё РЅР° enum СЃ СЏРІРЅС‹Рј USING
    op.execute(
        """
    DO $$
    DECLARE
        cur_type text;
    BEGIN
        SELECT t.typname INTO cur_type
        FROM pg_attribute a
        JOIN pg_class c ON c.oid = a.attrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        JOIN pg_type t ON t.oid = a.atttypid
        WHERE c.relname = 'campaigns'
          AND n.nspname = current_schema()
          AND a.attname = 'status'
          AND NOT a.attisdropped;

        IF cur_type IS NOT NULL AND cur_type <> 'campaign_status' THEN
            EXECUTE 'ALTER TABLE campaigns ALTER COLUMN status TYPE campaign_status USING status::text::campaign_status';
        END IF;
    END;
    $$;
    """
    )

    # 4) РІРѕР·РІСЂР°С‰Р°РµРј РєРѕСЂСЂРµРєС‚РЅС‹Р№ DEFAULT РґР»СЏ РЅРѕРІРѕРіРѕ enum
    op.execute("ALTER TABLE campaigns ALTER COLUMN status SET DEFAULT 'DRAFT'::campaign_status")

    op.drop_column("campaigns", "delete_reason")
    op.drop_column("campaigns", "deleted_by")
    op.drop_column("campaigns", "deleted_at")
    op.drop_column("campaigns", "failed_count")
    op.drop_column("campaigns", "delivered_count")
    op.drop_column("campaigns", "sent_count")
    op.drop_column("campaigns", "total_messages")
    op.drop_column("campaigns", "company_id")
    op.add_column(
        "billing_payments",
        sa.Column("payment_type", sa.VARCHAR(length=32), autoincrement=False, nullable=False),
    )
    op.add_column(
        "billing_payments",
        sa.Column("receipt_url", sa.VARCHAR(length=1024), autoincrement=False, nullable=True),
    )
    op.add_column(
        "billing_payments",
        sa.Column(
            "provider_transaction_id", sa.VARCHAR(length=128), autoincrement=False, nullable=True
        ),
    )
    op.add_column(
        "billing_payments",
        sa.Column(
            "provider_invoice_id", sa.VARCHAR(length=128), autoincrement=False, nullable=False
        ),
    )
    op.add_column(
        "billing_payments",
        sa.Column("receipt_number", sa.VARCHAR(length=64), autoincrement=False, nullable=True),
    )
    op.add_column(
        "billing_payments",
        sa.Column(
            "billing_period_start",
            postgresql.TIMESTAMP(timezone=True),
            autoincrement=False,
            nullable=True,
        ),
    )
    op.add_column(
        "billing_payments",
        sa.Column(
            "processed_at", postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=True
        ),
    )
    op.add_column(
        "billing_payments",
        sa.Column(
            "billing_period_end",
            postgresql.TIMESTAMP(timezone=True),
            autoincrement=False,
            nullable=True,
        ),
    )
    op.drop_constraint(
        op.f("fk__billing_payments__order_id__orders"), "billing_payments", type_="foreignkey"
    )
    op.drop_constraint("uq_bp_provider_payment", "billing_payments", type_="unique")
    op.drop_index(op.f("ix_billing_payments_updated_at"), table_name="billing_payments")
    op.drop_index("ix_billing_payments_status_created", table_name="billing_payments")
    op.drop_index(op.f("ix_billing_payments_refunded_at"), table_name="billing_payments")
    op.drop_index(op.f("ix_billing_payments_provider_payment_id"), table_name="billing_payments")
    op.drop_index(op.f("ix_billing_payments_provider"), table_name="billing_payments")
    op.drop_index(op.f("ix_billing_payments_order_id"), table_name="billing_payments")
    op.drop_index("ix_billing_payments_method_created", table_name="billing_payments")
    op.drop_index(op.f("ix_billing_payments_method"), table_name="billing_payments")
    op.drop_index(op.f("ix_billing_payments_failed_at"), table_name="billing_payments")
    op.drop_index(op.f("ix_billing_payments_deleted_at"), table_name="billing_payments")
    op.drop_index(op.f("ix_billing_payments_created_at"), table_name="billing_payments")
    op.drop_index("ix_billing_payments_company_created", table_name="billing_payments")
    op.drop_index(op.f("ix_billing_payments_captured_at"), table_name="billing_payments")
    op.drop_index(op.f("ix_billing_payments_authorized_at"), table_name="billing_payments")
    op.create_index(
        op.f("ix_billing_payments_provider_invoice_id"),
        "billing_payments",
        ["provider_invoice_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_billing_payments_payment_type"), "billing_payments", ["payment_type"], unique=False
    )
    op.create_unique_constraint(
        op.f("billing_payments_provider_invoice_id_key"),
        "billing_payments",
        ["provider_invoice_id"],
        postgresql_nulls_not_distinct=False,
    )
    op.alter_column(
        "billing_payments",
        "description",
        existing_type=sa.Text(),
        type_=sa.VARCHAR(length=255),
        existing_nullable=True,
    )
    op.alter_column(
        "billing_payments",
        "updated_at",
        existing_type=postgresql.TIMESTAMP(timezone=True),
        server_default=sa.text("now()"),
        existing_nullable=False,
    )
    op.alter_column(
        "billing_payments",
        "created_at",
        existing_type=postgresql.TIMESTAMP(timezone=True),
        server_default=sa.text("now()"),
        existing_nullable=False,
    )
    op.alter_column(
        "billing_payments",
        "provider",
        existing_type=sa.String(length=64),
        server_default=sa.text("'tiptop'::character varying"),
        type_=sa.VARCHAR(length=32),
        nullable=False,
    )
    op.alter_column(
        "billing_payments",
        "currency",
        existing_type=sa.VARCHAR(length=8),
        server_default=sa.text("'KZT'::character varying"),
        existing_nullable=False,
    )
    op.alter_column(
        "billing_payments",
        "amount",
        existing_type=sa.Numeric(precision=14, scale=2),
        type_=sa.NUMERIC(precision=10, scale=2),
        existing_nullable=False,
    )
    op.drop_column("billing_payments", "deleted_at")
    op.drop_column("billing_payments", "meta")
    op.drop_column("billing_payments", "failed_at")
    op.drop_column("billing_payments", "refunded_at")
    op.drop_column("billing_payments", "captured_at")
    op.drop_column("billing_payments", "authorized_at")
    op.drop_column("billing_payments", "provider_receipt_url")
    op.drop_column("billing_payments", "provider_payment_id")
    op.drop_column("billing_payments", "method")
    op.drop_column("billing_payments", "order_id")
    op.drop_constraint(
        op.f("fk__audit_logs__product_id__products"), "audit_logs", type_="foreignkey"
    )
    op.create_foreign_key(
        op.f("audit_logs_product_id_fkey"),
        "audit_logs",
        "products",
        ["product_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        op.f("fk_audit_logs_company_id_companies"),
        "audit_logs",
        "companies",
        ["company_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.drop_index("ix_audit_wh_created", table_name="audit_logs")
    op.drop_index(op.f("ix_audit_logs_user_id"), table_name="audit_logs")
    op.drop_index(op.f("ix_audit_logs_updated_at"), table_name="audit_logs")
    op.drop_index(op.f("ix_audit_logs_source"), table_name="audit_logs")
    op.drop_index(op.f("ix_audit_logs_request_id"), table_name="audit_logs")
    op.drop_index(op.f("ix_audit_logs_product_id"), table_name="audit_logs")
    op.drop_index(op.f("ix_audit_logs_payment_id"), table_name="audit_logs")
    op.drop_index(op.f("ix_audit_logs_order_id"), table_name="audit_logs")
    op.drop_index(op.f("ix_audit_logs_id"), table_name="audit_logs")
    op.drop_index(op.f("ix_audit_logs_entity_type"), table_name="audit_logs")
    op.drop_index(op.f("ix_audit_logs_entity_id"), table_name="audit_logs")
    op.drop_index(op.f("ix_audit_logs_created_at"), table_name="audit_logs")
    op.drop_index(op.f("ix_audit_logs_correlation_id"), table_name="audit_logs")
    op.drop_index(op.f("ix_audit_logs_company_id"), table_name="audit_logs")
    op.drop_index("ix_audit_entity_type_id", table_name="audit_logs")
    op.drop_index("ix_audit_created_at", table_name="audit_logs")
    op.drop_index("ix_audit_action_user", table_name="audit_logs")
    op.create_index(
        op.f("ix_audit_logs_entity"), "audit_logs", ["entity_type", "entity_id"], unique=False
    )
    op.create_index(op.f("idx_audit_logs_request"), "audit_logs", ["request_id"], unique=False)
    op.create_index(op.f("idx_audit_logs_product_id"), "audit_logs", ["product_id"], unique=False)
    op.create_index(
        op.f("idx_audit_logs_entity"), "audit_logs", ["entity_type", "entity_id"], unique=False
    )
    op.alter_column(
        "audit_logs",
        "updated_at",
        existing_type=postgresql.TIMESTAMP(),
        server_default=sa.text("now()"),
        existing_nullable=False,
    )
    op.alter_column(
        "audit_logs",
        "created_at",
        existing_type=postgresql.TIMESTAMP(),
        server_default=sa.text("now()"),
        existing_nullable=False,
    )
    op.alter_column(
        "audit_logs",
        "source",
        existing_type=sa.String(length=64),
        type_=sa.TEXT(),
        existing_nullable=True,
    )
    op.alter_column(
        "audit_logs",
        "correlation_id",
        existing_type=sa.String(length=64),
        type_=sa.TEXT(),
        existing_nullable=True,
    )
    (
        op.create_table(
            "campaign_messages",
            sa.Column("id", sa.INTEGER(), autoincrement=True, nullable=False),
            sa.Column("campaign_id", sa.INTEGER(), autoincrement=False, nullable=False),
            sa.Column("recipient", sa.VARCHAR(length=500), autoincrement=False, nullable=False),
            sa.Column("content", sa.TEXT(), autoincrement=False, nullable=False),
            sa.Column(
                "status",
                sa.VARCHAR(length=50),
                server_default=sa.text("'pending'::character varying"),
                autoincrement=False,
                nullable=False,
            ),
            sa.Column(
                "channel",
                sa.VARCHAR(length=50),
                server_default=sa.text("'email'::character varying"),
                autoincrement=False,
                nullable=False,
            ),
            sa.Column("scheduled_for", sa.VARCHAR(length=40), autoincrement=False, nullable=True),
            sa.Column("error", sa.TEXT(), autoincrement=False, nullable=True),
            sa.PrimaryKeyConstraint("id", name=op.f("campaign_messages_pkey")),
            sa.UniqueConstraint(
                "campaign_id",
                "recipient",
                "channel",
                name=op.f("uq_msg_camp_recipient_channel"),
                postgresql_include=[],
                postgresql_nulls_not_distinct=False,
            ),
        ),
    )
    (op.create_index(op.f("ix_messages_channel"), "campaign_messages", ["channel"], unique=False),)
    (
        op.create_index(
            op.f("ix_campaign_messages_campaign_id"),
            "campaign_messages",
            ["campaign_id"],
            unique=False,
        ),
    )
    op.create_table(
        "bot_sessions",
        sa.Column("id", sa.INTEGER(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.INTEGER(), autoincrement=False, nullable=False),
        sa.Column("company_id", sa.INTEGER(), autoincrement=False, nullable=False),
        sa.Column("session_type", sa.VARCHAR(length=32), autoincrement=False, nullable=False),
        sa.Column("status", sa.VARCHAR(length=32), autoincrement=False, nullable=False),
        sa.Column(
            "context", postgresql.JSON(astext_type=sa.Text()), autoincrement=False, nullable=True
        ),
        sa.Column("intent", sa.VARCHAR(length=64), autoincrement=False, nullable=True),
        sa.Column("title", sa.VARCHAR(length=255), autoincrement=False, nullable=True),
        sa.Column(
            "language",
            sa.VARCHAR(length=8),
            server_default=sa.text("'ru'::character varying"),
            autoincrement=False,
            nullable=False,
        ),
        sa.Column(
            "last_activity_at",
            postgresql.TIMESTAMP(timezone=True),
            autoincrement=False,
            nullable=True,
        ),
        sa.Column(
            "completed_at", postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=True
        ),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            autoincrement=False,
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            autoincrement=False,
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            name=op.f("fk_bot_sessions_company_id"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_bot_sessions_user_id"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("bot_sessions_pkey")),
    )
    op.create_index(op.f("ix_bot_sessions_user_id"), "bot_sessions", ["user_id"], unique=False)
    op.create_index(op.f("ix_bot_sessions_status"), "bot_sessions", ["status"], unique=False)
    op.create_index(
        op.f("ix_bot_sessions_session_type"), "bot_sessions", ["session_type"], unique=False
    )
    op.create_index(
        op.f("ix_bot_sessions_last_activity_at"), "bot_sessions", ["last_activity_at"], unique=False
    )
    op.create_index(op.f("ix_bot_sessions_intent"), "bot_sessions", ["intent"], unique=False)
    op.create_index(op.f("ix_bot_sessions_id"), "bot_sessions", ["id"], unique=False)
    op.create_index(
        op.f("ix_bot_sessions_company_id"), "bot_sessions", ["company_id"], unique=False
    )
    op.drop_index("ix_recon_provider_external", table_name="provider_reconciliation")
    op.drop_index(op.f("ix_provider_reconciliation_status"), table_name="provider_reconciliation")
    op.drop_index(
        op.f("ix_provider_reconciliation_statement_at"), table_name="provider_reconciliation"
    )
    op.drop_index(op.f("ix_provider_reconciliation_provider"), table_name="provider_reconciliation")
    op.drop_index(
        op.f("ix_provider_reconciliation_matched_payment_id"), table_name="provider_reconciliation"
    )
    op.drop_index(op.f("ix_provider_reconciliation_id"), table_name="provider_reconciliation")
    op.drop_index(
        op.f("ix_provider_reconciliation_external_id"), table_name="provider_reconciliation"
    )
    op.drop_index(
        op.f("ix_provider_reconciliation_created_at"), table_name="provider_reconciliation"
    )
    op.drop_table("provider_reconciliation")
    op.drop_index(op.f("ix_order_status_history_order_id"), table_name="order_status_history")
    op.drop_index(op.f("ix_order_status_history_changed_by"), table_name="order_status_history")
    op.drop_index(op.f("ix_order_status_history_changed_at"), table_name="order_status_history")
    op.drop_table("order_status_history")
    op.drop_index(op.f("ix_billing_invoices_updated_at"), table_name="billing_invoices")
    op.drop_index("ix_billing_invoices_status_due", table_name="billing_invoices")
    op.drop_index(op.f("ix_billing_invoices_status"), table_name="billing_invoices")
    op.drop_index(op.f("ix_billing_invoices_paid_at"), table_name="billing_invoices")
    op.drop_index(op.f("ix_billing_invoices_order_id"), table_name="billing_invoices")
    op.drop_index(op.f("ix_billing_invoices_number"), table_name="billing_invoices")
    op.drop_index(op.f("ix_billing_invoices_issued_at"), table_name="billing_invoices")
    op.drop_index(op.f("ix_billing_invoices_id"), table_name="billing_invoices")
    op.drop_index(op.f("ix_billing_invoices_due_at"), table_name="billing_invoices")
    op.drop_index(op.f("ix_billing_invoices_deleted_at"), table_name="billing_invoices")
    op.drop_index(op.f("ix_billing_invoices_created_at"), table_name="billing_invoices")
    op.drop_index("ix_billing_invoices_company_status", table_name="billing_invoices")
    op.drop_index(op.f("ix_billing_invoices_company_id"), table_name="billing_invoices")
    op.drop_table("billing_invoices")
    op.drop_index(op.f("ix_otp_attempts_id"), table_name="otp_attempts")
    op.drop_index("ix_otp_attempt_verified", table_name="otp_attempts")
    op.drop_index("ix_otp_attempt_purpose", table_name="otp_attempts")
    op.drop_index("ix_otp_attempt_phone", table_name="otp_attempts")
    op.drop_index("ix_otp_attempt_expires_at", table_name="otp_attempts")
    op.drop_index("ix_otp_attempt_created_at", table_name="otp_attempts")
    op.drop_index("ix_otp_attempt_channel", table_name="otp_attempts")
    op.drop_index("ix_otp_attempt_blocked", table_name="otp_attempts")
    op.drop_table("otp_attempts")
    op.drop_index("ix_outbox_status_due", table_name="integration_outbox")
    op.drop_index(op.f("ix_integration_outbox_status"), table_name="integration_outbox")
    op.drop_index(op.f("ix_integration_outbox_id"), table_name="integration_outbox")
    op.drop_index(op.f("ix_integration_outbox_event_type"), table_name="integration_outbox")
    op.drop_index(op.f("ix_integration_outbox_created_at"), table_name="integration_outbox")
    op.drop_index(op.f("ix_integration_outbox_aggregate_type"), table_name="integration_outbox")
    op.drop_index(op.f("ix_integration_outbox_aggregate_id"), table_name="integration_outbox")
    op.drop_table("integration_outbox")
    # ### end Alembic commands ###

    # === Helper: safe cast text/varchar -> named ENUM with cleanup ===


def coerce_column_to_enum(
    table: str, column: str, enum_name: str, allowed: tuple[str, ...], default_value: str
) -> None:
    """
    Ensures all values are within the allowed enum labels, then ALTERs column type
    with explicit USING cast to the named enum.
    """
    conn = op.get_bind()
    # Normalize unexpected values to default_value
    placeholders = ", ".join(["%s"] * len(allowed))
    sql_cleanup = f"""
        UPDATE {table}
           SET {column} = %s
         WHERE ({column})::text NOT IN ({placeholders})
            OR {column} IS NULL
"""
    conn.exec_driver_sql(sql_cleanup, (default_value, *allowed))
    # Now perform ALTER with explicit USING
    op.execute(
        f"""ALTER TABLE {table}
                       ALTER COLUMN {column} TYPE {enum_name}
                       USING ({column})::text::{enum_name}"""
    )


# === End helper ===

revision = "48e583d830c1"
down_revision = "b1f1a6b0d3a1"
branch_labels = None
depends_on = None


# --- SAFETY PATCH: restore missing upgrade() for revision 48e583d830c1 ---
def upgrade() -> None:
    """
    Recreated upgrade() for 48e583d830c1.

    - Creates required ENUM types if they don't exist yet.
    - Adds missing values to paymentstatus (AUTHORIZED, CAPTURED, CHARGEBACK) if needed.

    This block is idempotent and safe to run multiple times.
    """
    from sqlalchemy import text

    from alembic import op

    conn = op.get_bind()

    statements = [
        # paymentstatus enum: make sure type exists (with a sane set of values)
        """
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'paymentstatus') THEN
                CREATE TYPE paymentstatus AS ENUM (
                    'PENDING','AUTHORIZED','CAPTURED','FAILED','REFUNDED','CHARGEBACK'
                );
            END IF;
        END$$;
        """,
        # add values that были в логах этой ревизии
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_type WHERE typname = 'paymentstatus') THEN
                ALTER TYPE paymentstatus ADD VALUE IF NOT EXISTS 'AUTHORIZED';
                ALTER TYPE paymentstatus ADD VALUE IF NOT EXISTS 'CAPTURED';
                ALTER TYPE paymentstatus ADD VALUE IF NOT EXISTS 'CHARGEBACK';
            END IF;
        END$$;
        """,
        # other enums created by this revision (создаём пустоты, если их нет)
        """
        DO $$ BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'paymentprovider') THEN
                -- создаём c базовыми значениями; при необходимости последующие ревизии дополнят
                CREATE TYPE paymentprovider AS ENUM ('GENERIC');
            END IF;
        END$$;
        """,
        """
        DO $$ BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'reconciliationstatus') THEN
                CREATE TYPE reconciliationstatus AS ENUM ('PENDING','MATCHED','MISMATCHED');
            END IF;
        END$$;
        """,
        """
        DO $$ BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'campaign_status') THEN
                CREATE TYPE campaign_status AS ENUM ('DRAFT','ACTIVE','PAUSED','FINISHED','CANCELLED');
            END IF;
        END$$;
        """,
        """
        DO $$ BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'message_channel') THEN
                CREATE TYPE message_channel AS ENUM ('EMAIL','SMS','WHATSAPP','TELEGRAM');
            END IF;
        END$$;
        """,
        """
        DO $$ BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'message_status') THEN
                CREATE TYPE message_status AS ENUM ('QUEUED','SENT','DELIVERED','FAILED','READ');
            END IF;
        END$$;
        """,
        """
        DO $$ BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'paymentmethod') THEN
                CREATE TYPE paymentmethod AS ENUM ('CASH','CARD','BANK_TRANSFER','E_WALLET');
            END IF;
        END$$;
        """,
    ]

    for sql in statements:
        conn.execute(text(sql))


# --- /SAFETY PATCH ---
