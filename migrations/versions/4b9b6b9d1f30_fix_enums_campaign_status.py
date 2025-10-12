"""fix enums and campaign status cast safely

Revision ID: 4b9b6b9d1f30
Revises: 48e583d830c1
Create Date: 2025-10-10 04:30:00.000000+00:00
"""
from collections.abc import Sequence
from typing import Union

from sqlalchemy.dialects import postgresql as psql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "4b9b6b9d1f30"
down_revision: Union[str, Sequence[str], None] = "48e583d830c1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# --- Logging
import logging

log = logging.getLogger("alembic.migration")
log.setLevel(logging.INFO)

# --- ENUM labels (super-sets to avoid future breaks)
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
    row = conn.exec_driver_sql("SELECT 1 FROM pg_type WHERE typname = %s", (type_name,)).fetchone()
    return bool(row)


def ensure_enum_exists(enum_name: str, values: tuple[str, ...]) -> None:
    conn = op.get_bind()
    if not _pg_type_exists(conn, enum_name):
        log.info("Creating ENUM %s", enum_name)
        psql.ENUM(*values, name=enum_name).create(conn, checkfirst=True)


def add_enum_values(enum_name: str, values: tuple[str, ...]) -> None:
    conn = op.get_bind()
    existing = {
        r[0]
        for r in conn.exec_driver_sql(
            """
            SELECT e.enumlabel
            FROM pg_type t
            JOIN pg_enum e ON e.enumtypid = t.oid
            WHERE t.typname = %s
            """,
            (enum_name,),
        ).fetchall()
    }
    for v in values:
        if v not in existing:
            log.info("Adding value '%s' to ENUM %s", v, enum_name)
            conn.exec_driver_sql(f"ALTER TYPE {enum_name} ADD VALUE IF NOT EXISTS '{v}'")


def prepare_all_enums() -> None:
    for name, vals in ENUM_LABELS.items():
        ensure_enum_exists(name, vals)
        add_enum_values(name, vals)


def upgrade() -> None:
    # 1) Ensure all enums exist (idempotent) and have the full label sets
    prepare_all_enums()

    # 2) campaigns.status: drop default, normalize, cast to campaign_status, set default
    op.execute(
        """
        DO $$
        DECLARE col_type text;
        BEGIN
          SELECT t.typname INTO col_type
            FROM pg_attribute a
            JOIN pg_class c ON c.oid = a.attrelid
            JOIN pg_namespace n ON n.oid = c.relnamespace
            JOIN pg_type t ON t.oid = a.atttypid
           WHERE c.relname = 'campaigns'
             AND n.nspname = current_schema()
             AND a.attname = 'status'
             AND NOT a.attisdropped;

          IF col_type IS NULL THEN
            RAISE NOTICE 'Column campaigns.status not found';
          ELSIF col_type <> 'campaign_status' THEN
            -- Drop default unconditionally to avoid cast errors
            EXECUTE 'ALTER TABLE campaigns ALTER COLUMN status DROP DEFAULT';

            -- Normalize unexpected values prior to cast
            EXECUTE $upd$
              UPDATE campaigns
                 SET status = 'DRAFT'
               WHERE status IS NULL
                  OR status::text NOT IN ('DRAFT','ACTIVE','PAUSED','COMPLETED')
            $upd$;

            -- Alter column type with explicit USING cast
            EXECUTE 'ALTER TABLE campaigns ALTER COLUMN status TYPE campaign_status USING status::text::campaign_status';

            -- Set a sane default
            EXECUTE 'ALTER TABLE campaigns ALTER COLUMN status SET DEFAULT ''DRAFT''::campaign_status';
          END IF;
        END $$ LANGUAGE plpgsql;
        """
    )

    # 3) messages.status -> message_status (only if still old type/text)
    op.execute(
        """
        DO $$
        DECLARE col_type text;
        BEGIN
          SELECT t.typname INTO col_type
            FROM pg_attribute a
            JOIN pg_class c ON c.oid = a.attrelid
            JOIN pg_namespace n ON n.oid = c.relnamespace
            JOIN pg_type t ON t.oid = a.atttypid
           WHERE c.relname = 'messages'
             AND n.nspname = current_schema()
             AND a.attname = 'status'
             AND NOT a.attisdropped;

          IF col_type IS NULL THEN
            RAISE NOTICE 'Column messages.status not found';
          ELSIF col_type <> 'message_status' THEN
            -- Drop default just in case
            EXECUTE 'ALTER TABLE messages ALTER COLUMN status DROP DEFAULT';

            -- Normalize values before cast
            EXECUTE $upd$
              UPDATE messages
                 SET status = 'PENDING'
               WHERE status IS NULL
                  OR status::text NOT IN ('PENDING','QUEUED','SENT','DELIVERED','FAILED','OPENED','CLICKED')
            $upd$;

            -- Alter using explicit cast
            EXECUTE 'ALTER TABLE messages ALTER COLUMN status TYPE message_status USING status::text::message_status';
          END IF;
        END $$ LANGUAGE plpgsql;
        """
    )


def downgrade() -> None:
    # Reverse: campaigns.status back to TEXT with default 'DRAFT' (text), and messages.status back to TEXT
    op.execute(
        """
        DO $$
        DECLARE col_type text;
        BEGIN
          -- campaigns.status
          SELECT t.typname INTO col_type
            FROM pg_attribute a
            JOIN pg_class c ON c.oid = a.attrelid
            JOIN pg_namespace n ON n.oid = c.relnamespace
            JOIN pg_type t ON t.oid = a.atttypid
           WHERE c.relname = 'campaigns'
             AND n.nspname = current_schema()
             AND a.attname = 'status'
             AND NOT a.attisdropped;

          IF col_type = 'campaign_status' THEN
            EXECUTE 'ALTER TABLE campaigns ALTER COLUMN status DROP DEFAULT';
            EXECUTE 'ALTER TABLE campaigns ALTER COLUMN status TYPE TEXT USING status::text';
            EXECUTE 'ALTER TABLE campaigns ALTER COLUMN status SET DEFAULT ''DRAFT''';
          END IF;

          -- messages.status
          SELECT t.typname INTO col_type
            FROM pg_attribute a
            JOIN pg_class c ON c.oid = a.attrelid
            JOIN pg_namespace n ON n.oid = c.relnamespace
            JOIN pg_type t ON t.oid = a.atttypid
           WHERE c.relname = 'messages'
             AND n.nspname = current_schema()
             AND a.attname = 'status'
             AND NOT a.attisdropped;

          IF col_type = 'message_status' THEN
            EXECUTE 'ALTER TABLE messages ALTER COLUMN status TYPE TEXT USING status::text';
          END IF;
        END $$ LANGUAGE plpgsql;
        """
    )
