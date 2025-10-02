"""Add campaign and audit models from PR #18

Revision ID: 20240914_add_campaign_and_audit_models
Revises: c4c8623bc099
Create Date: 2024-09-14 06:30:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect, text
from sqlalchemy.dialects.postgresql import ENUM as PGEnum

# --- Alembic identifiers ---
revision = "20240914_add_campaign_and_audit_models"
down_revision = "c4c8623bc099"
branch_labels = None
depends_on = None

# ---------------------------
# Helpers (idempotent & safe)
# ---------------------------


def _inspector():
    bind = op.get_bind()
    return inspect(bind), bind


def table_exists(table_name: str, schema: str | None = None) -> bool:
    insp, _ = _inspector()
    return table_name in insp.get_table_names(schema=schema)


def column_exists(table_name: str, column_name: str, schema: str | None = None) -> bool:
    insp, _ = _inspector()
    if not table_exists(table_name, schema):
        return False
    return any(col["name"] == column_name for col in insp.get_columns(table_name, schema=schema))


def index_exists(table_name: str, index_name: str, schema: str | None = None) -> bool:
    insp, _ = _inspector()
    if not table_exists(table_name, schema):
        return False
    return any(ix["name"] == index_name for ix in insp.get_indexes(table_name, schema=schema))


def fk_exists(table_name: str, fk_name: str, schema: str | None = None) -> bool:
    insp, _ = _inspector()
    if not table_exists(table_name, schema):
        return False
    return any(fk.get("name") == fk_name for fk in insp.get_foreign_keys(table_name, schema=schema))


def enum_exists(enum_name: str) -> bool:
    _, bind = _inspector()
    res = bind.execute(
        sa.text("SELECT EXISTS (SELECT 1 FROM pg_type WHERE typname = :name)"),
        {"name": enum_name},
    )
    return bool(res.scalar())


def create_enum_safe(enum_name: str, values: list[str]) -> None:
    """Create Postgres ENUM type only if it doesn't exist."""
    _, bind = _inspector()
    sql = f"""
    DO $$
    BEGIN
        IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = '{enum_name}') THEN
            CREATE TYPE {enum_name} AS ENUM ({', '.join(f"'{v}'" for v in values)});
        END IF;
    END
    $$;
    """
    bind.execute(text(sql))


def drop_enum_safe(enum_name: str) -> None:
    """Drop Postgres ENUM type only if it exists and is unused."""
    _, bind = _inspector()
    # Drop only if no columns use this type (to be extra safe)
    sql = f"""
    DO $$
    BEGIN
        IF EXISTS (SELECT 1 FROM pg_type WHERE typname = '{enum_name}')
           AND NOT EXISTS (
               SELECT 1
               FROM pg_attribute a
               JOIN pg_type t ON a.atttypid = t.oid
               WHERE t.typname = '{enum_name}' AND a.attnum > 0 AND NOT a.attisdropped
           )
        THEN
            DROP TYPE {enum_name};
        END IF;
    END
    $$;
    """
    bind.execute(text(sql))


def create_index_safe(
    index_name: str, table_name: str, columns: list[str], unique: bool = False
) -> None:
    if not table_exists(table_name):
        return
    if index_exists(table_name, index_name):
        return
    op.create_index(index_name, table_name, columns, unique=unique)


def add_column_safe(table_name: str, column: sa.Column, schema: str | None = None) -> None:
    if table_exists(table_name, schema) and not column_exists(table_name, column.name, schema):
        op.add_column(table_name, column)


def create_fk_safe(
    fk_name: str,
    source_table: str,
    referent_table: str,
    local_cols: list[str],
    remote_cols: list[str],
    ondelete: str | None = None,
    source_schema: str | None = None,
    referent_schema: str | None = None,
) -> None:
    if not table_exists(source_table, source_schema) or not table_exists(
        referent_table, referent_schema
    ):
        return
    if fk_exists(source_table, fk_name, source_schema):
        return
    op.create_foreign_key(
        fk_name,
        source_table,
        referent_table,
        local_cols,
        remote_cols,
        ondelete=ondelete,
        source_schema=source_schema,
        referent_schema=referent_schema,
    )


# ---------------------------
# Upgrade / Downgrade
# ---------------------------


def upgrade():
    """Add campaign and audit models from PR #18, idempotent and safe."""
    # 1) Ensure ENUM types exist exactly once
    create_enum_safe("campaignstatus", ["DRAFT", "ACTIVE", "PAUSED", "COMPLETED"])
    create_enum_safe("messagestatus", ["PENDING", "SENT", "DELIVERED", "FAILED"])

    # PGEnum handles referencing existing types without re-creating them
    CampaignStatus = PGEnum(name="campaignstatus", create_type=False)
    MessageStatus = PGEnum(name="messagestatus", create_type=False)

    # 2) users (minimal schema for FK). If table already exists, ensure minimal columns.
    if not table_exists("users"):
        op.create_table(
            "users",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("username", sa.String(255), nullable=False, unique=True),
            sa.Column("email", sa.String(255), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        )
    else:
        add_column_safe(
            "users", sa.Column("id", sa.Integer(), primary_key=True)
        )  # harmless if already PK
        add_column_safe("users", sa.Column("username", sa.String(255), nullable=False))
        add_column_safe("users", sa.Column("email", sa.String(255)))
        add_column_safe(
            "users",
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        )

    # 3) companies (for audit_logs FK). Create or ensure essential columns
    if not table_exists("companies"):
        op.create_table(
            "companies",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("name", sa.String(255), nullable=False, index=True),
            sa.Column("bin_iin", sa.String(32), nullable=True, unique=True, index=True),
            sa.Column("phone", sa.String(32), nullable=True),
            sa.Column("email", sa.String(255), nullable=True),
            sa.Column("address", sa.Text(), nullable=True),
            sa.Column(
                "is_active",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("TRUE"),
                index=True,
            ),
            sa.Column("kaspi_store_id", sa.String(64), nullable=True, unique=True, index=True),
            sa.Column("kaspi_api_key", sa.String(255), nullable=True),
            sa.Column(
                "subscription_plan",
                sa.String(32),
                nullable=False,
                server_default=sa.text("'start'"),
            ),
            sa.Column("subscription_expires_at", sa.String(32), nullable=True),
            sa.Column("settings", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        )
    else:
        add_column_safe("companies", sa.Column("id", sa.Integer(), primary_key=True))
        add_column_safe("companies", sa.Column("name", sa.String(255), nullable=False))
        add_column_safe("companies", sa.Column("bin_iin", sa.String(32)))
        add_column_safe("companies", sa.Column("phone", sa.String(32)))
        add_column_safe("companies", sa.Column("email", sa.String(255)))
        add_column_safe("companies", sa.Column("address", sa.Text()))
        add_column_safe(
            "companies",
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("TRUE")),
        )
        add_column_safe("companies", sa.Column("kaspi_store_id", sa.String(64)))
        add_column_safe("companies", sa.Column("kaspi_api_key", sa.String(255)))
        add_column_safe(
            "companies",
            sa.Column(
                "subscription_plan",
                sa.String(32),
                nullable=False,
                server_default=sa.text("'start'"),
            ),
        )
        add_column_safe("companies", sa.Column("subscription_expires_at", sa.String(32)))
        add_column_safe("companies", sa.Column("settings", sa.Text()))
        add_column_safe(
            "companies",
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        )
        add_column_safe(
            "companies",
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        )

    # 4) campaigns
    if not table_exists("campaigns"):
        op.create_table(
            "campaigns",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("title", sa.String(255), nullable=False),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("status", CampaignStatus, nullable=False, server_default=sa.text("'DRAFT'")),
            sa.Column("scheduled_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        )
    else:
        add_column_safe("campaigns", sa.Column("id", sa.Integer(), primary_key=True))
        add_column_safe("campaigns", sa.Column("title", sa.String(255), nullable=False))
        add_column_safe("campaigns", sa.Column("description", sa.Text()))
        add_column_safe(
            "campaigns",
            sa.Column("status", CampaignStatus, nullable=False, server_default=sa.text("'DRAFT'")),
        )
        add_column_safe("campaigns", sa.Column("scheduled_at", sa.DateTime()))
        add_column_safe(
            "campaigns",
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        )
        add_column_safe(
            "campaigns",
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        )

    create_index_safe("ix_campaigns_status", "campaigns", ["status"])

    # 5) messages
    if not table_exists("messages"):
        op.create_table(
            "messages",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("campaign_id", sa.Integer(), nullable=False),
            sa.Column("recipient", sa.String(255), nullable=False),
            sa.Column("content", sa.Text(), nullable=False),
            sa.Column("status", MessageStatus, nullable=False, server_default=sa.text("'PENDING'")),
            sa.Column("sent_at", sa.DateTime(), nullable=True),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        )
        create_fk_safe(
            "fk_messages_campaign_id_campaigns",
            source_table="messages",
            referent_table="campaigns",
            local_cols=["campaign_id"],
            remote_cols=["id"],
            ondelete=None,
        )
    else:
        add_column_safe("messages", sa.Column("id", sa.Integer(), primary_key=True))
        add_column_safe("messages", sa.Column("campaign_id", sa.Integer(), nullable=False))
        add_column_safe("messages", sa.Column("recipient", sa.String(255), nullable=False))
        add_column_safe("messages", sa.Column("content", sa.Text(), nullable=False))
        add_column_safe(
            "messages",
            sa.Column("status", MessageStatus, nullable=False, server_default=sa.text("'PENDING'")),
        )
        add_column_safe("messages", sa.Column("sent_at", sa.DateTime()))
        add_column_safe("messages", sa.Column("error_message", sa.Text()))
        add_column_safe(
            "messages",
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        )
        add_column_safe(
            "messages",
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        )
        create_fk_safe(
            "fk_messages_campaign_id_campaigns",
            source_table="messages",
            referent_table="campaigns",
            local_cols=["campaign_id"],
            remote_cols=["id"],
            ondelete=None,
        )

    create_index_safe("ix_messages_campaign_id", "messages", ["campaign_id"])
    create_index_safe("ix_messages_status", "messages", ["status"])

    # 6) audit_logs
    if not table_exists("audit_logs"):
        op.create_table(
            "audit_logs",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("user_id", sa.Integer(), nullable=True),
            sa.Column("company_id", sa.Integer(), nullable=True),
            sa.Column("action", sa.String(100), nullable=False),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("entity_type", sa.String(50), nullable=True),
            sa.Column("entity_id", sa.Integer(), nullable=True),
            sa.Column("old_values", sa.JSON(), nullable=True),
            sa.Column("new_values", sa.JSON(), nullable=True),
            sa.Column("ip_address", sa.String(45), nullable=True),
            sa.Column("user_agent", sa.Text(), nullable=True),
            sa.Column("request_id", sa.String(64), nullable=True),
            sa.Column("details", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        )
        create_fk_safe(
            "fk_audit_logs_user_id_users",
            source_table="audit_logs",
            referent_table="users",
            local_cols=["user_id"],
            remote_cols=["id"],
            ondelete="SET NULL",
        )
        create_fk_safe(
            "fk_audit_logs_company_id_companies",
            source_table="audit_logs",
            referent_table="companies",
            local_cols=["company_id"],
            remote_cols=["id"],
            ondelete="CASCADE",
        )
    else:
        # Ensure essential columns (no data loss)
        add_column_safe("audit_logs", sa.Column("user_id", sa.Integer()))
        add_column_safe("audit_logs", sa.Column("company_id", sa.Integer()))
        add_column_safe("audit_logs", sa.Column("action", sa.String(100), nullable=False))
        add_column_safe("audit_logs", sa.Column("description", sa.Text()))
        add_column_safe("audit_logs", sa.Column("entity_type", sa.String(50)))
        add_column_safe("audit_logs", sa.Column("entity_id", sa.Integer()))
        add_column_safe("audit_logs", sa.Column("old_values", sa.JSON()))
        add_column_safe("audit_logs", sa.Column("new_values", sa.JSON()))
        add_column_safe("audit_logs", sa.Column("ip_address", sa.String(45)))
        add_column_safe("audit_logs", sa.Column("user_agent", sa.Text()))
        add_column_safe("audit_logs", sa.Column("request_id", sa.String(64)))
        add_column_safe("audit_logs", sa.Column("details", sa.JSON()))
        add_column_safe(
            "audit_logs",
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        )
        add_column_safe(
            "audit_logs",
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        )
        # Ensure FKs
        create_fk_safe(
            "fk_audit_logs_user_id_users",
            source_table="audit_logs",
            referent_table="users",
            local_cols=["user_id"],
            remote_cols=["id"],
            ondelete="SET NULL",
        )
        create_fk_safe(
            "fk_audit_logs_company_id_companies",
            source_table="audit_logs",
            referent_table="companies",
            local_cols=["company_id"],
            remote_cols=["id"],
            ondelete="CASCADE",
        )

    # indexes for audit_logs
    create_index_safe("ix_audit_logs_action", "audit_logs", ["action"])
    create_index_safe("ix_audit_logs_entity", "audit_logs", ["entity_type", "entity_id"])


def downgrade():
    """Reverse campaign & audit models (safe)."""

    # Drop indexes first (if they exist)
    for name, table in [
        ("ix_audit_logs_entity", "audit_logs"),
        ("ix_audit_logs_action", "audit_logs"),
        ("ix_messages_status", "messages"),
        ("ix_messages_campaign_id", "messages"),
        ("ix_campaigns_status", "campaigns"),
    ]:
        if index_exists(table, name):
            op.drop_index(name, table_name=table)

    # Drop FKs explicitly if present (helps on partially existing schemas)
    for fk_name, table in [
        ("fk_audit_logs_user_id_users", "audit_logs"),
        ("fk_audit_logs_company_id_companies", "audit_logs"),
        ("fk_messages_campaign_id_campaigns", "messages"),
    ]:
        if fk_exists(table, fk_name):
            op.drop_constraint(fk_name, table, type_="foreignkey")

    # Drop tables in reverse dependency order (if they exist)
    for table in ["audit_logs", "messages", "campaigns", "companies", "users"]:
        if table_exists(table):
            op.drop_table(table)

    # Drop ENUM types only if unused
    drop_enum_safe("campaignstatus")
    drop_enum_safe("messagestatus")
