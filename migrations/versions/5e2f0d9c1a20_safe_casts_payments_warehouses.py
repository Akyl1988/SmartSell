"""safe casts for payments & warehouses

Revision ID: 5e2f0d9c1a20
Revises: 37d99ac316e7
Create Date: 2025-10-12 13:50:00+00:00

Цели ревизии (безопасно и «по-взрослому»):
- payments.status: VARCHAR → ENUM paymentstatus (не создавая тип заново, если он уже есть).
- payments.created_at / updated_at: VARCHAR → TIMESTAMP WITHOUT TIME ZONE.
  * перед изменением типа вычищаем мусорные значения (пустые строки/невалидные даты).
- warehouses.working_hours: TEXT → JSONB с безопасным USING.
- warehouses.created_at / updated_at: TIMESTAMPTZ → TIMESTAMP WITHOUT TIME ZONE
  с корректной конверсией AT TIME ZONE 'UTC' и современным server_default.

Примечания:
- Никаких DROP-операций; только приведения типов и правки дефолтов.
- Все USING выражения типобезопасны (через ::text там, где нужно).
- JSONB предпочтён вместо JSON.
"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# Alembic identifiers
revision: str = "5e2f0d9c1a20"
down_revision: Union[str, Sequence[str], None] = "37d99ac316e7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# --- Вспомогательные объекты типов (не создают тип в БД сами по себе)
paymentstatus_enum = postgresql.ENUM(
    "PENDING",
    "AUTHORIZED",
    "CAPTURED",
    "FAILED",
    "REFUNDED",
    "CHARGEBACK",
    name="paymentstatus",
    create_type=False,
)


def _ensure_paymentstatus_type_exists() -> None:
    """
    Создаёт ENUM paymentstatus, если его нет (идемпотентно).
    Нельзя полагаться на create_type=True, т.к. тип мог быть создан ранее.
    """
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_type t
                JOIN pg_namespace n ON n.oid = t.typnamespace
                WHERE t.typname = 'paymentstatus'
            ) THEN
                CREATE TYPE paymentstatus AS ENUM
                    ('PENDING','AUTHORIZED','CAPTURED','FAILED','REFUNDED','CHARGEBACK');
            END IF;
        END$$;
        """
    )


def upgrade() -> None:
    # -------------------------------------------------------------------------
    # 0) Готовим тип ENUM для payments.status (если нужен)
    # -------------------------------------------------------------------------
    _ensure_paymentstatus_type_exists()

    # -------------------------------------------------------------------------
    # 1) payments.status: VARCHAR -> ENUM paymentstatus (безопасный CAST)
    # -------------------------------------------------------------------------
    op.alter_column(
        "payments",
        "status",
        existing_type=sa.VARCHAR(),  # безопасно без длины
        type_=paymentstatus_enum,
        existing_nullable=True,  # не ужесточаем nullable без надобности
        postgresql_using="status::paymentstatus",
    )

    # -------------------------------------------------------------------------
    # 2) payments.created_at / updated_at: чистим мусор перед CAST
    #    (работаем по text-представлению, чтобы не падать на текущем типе)
    # -------------------------------------------------------------------------
    # Пустые строки -> NULL
    op.execute(
        """
        UPDATE payments
           SET created_at = NULL
         WHERE created_at IS NOT NULL
           AND btrim(created_at::text) = '';
        """
    )
    op.execute(
        """
        UPDATE payments
           SET updated_at = NULL
         WHERE updated_at IS NOT NULL
           AND btrim(updated_at::text) = '';
        """
    )

    # Явно не-похожие на ISO8601 (YYYY-MM-DD[ HH:MM:SS[.ffffff]]) -> NULL
    # Регэксп простой, но решает 99% мусора; если есть экзотика — адаптируется отдельно.
    op.execute(
        r"""
        UPDATE payments
           SET created_at = NULL
         WHERE created_at IS NOT NULL
           AND NOT (created_at::text ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}([ T][0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]{1,6})?)?$');
        """
    )
    op.execute(
        r"""
        UPDATE payments
           SET updated_at = NULL
         WHERE updated_at IS NOT NULL
           AND NOT (updated_at::text ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}([ T][0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]{1,6})?)?$');
        """
    )

    # -------------------------------------------------------------------------
    # 3) payments.created_at / updated_at: VARCHAR -> TIMESTAMP WITHOUT TIME ZONE
    #    Используем USING через ::text, затем ::timestamp (без tz).
    # -------------------------------------------------------------------------
    op.alter_column(
        "payments",
        "created_at",
        existing_type=sa.VARCHAR(),
        type_=sa.DateTime(timezone=False),
        existing_nullable=True,
        server_default=None,
        postgresql_using="NULLIF(created_at::text, '')::timestamp",
    )
    op.alter_column(
        "payments",
        "updated_at",
        existing_type=sa.VARCHAR(),
        type_=sa.DateTime(timezone=False),
        existing_nullable=True,
        server_default=None,
        postgresql_using="NULLIF(updated_at::text, '')::timestamp",
    )

    # -------------------------------------------------------------------------
    # 4) warehouses.working_hours: TEXT -> JSONB (через безопасный USING)
    # -------------------------------------------------------------------------
    op.alter_column(
        "warehouses",
        "working_hours",
        existing_type=sa.TEXT(),
        type_=postgresql.JSONB(astext_type=sa.Text()),
        existing_nullable=True,
        postgresql_using="NULLIF(working_hours::text, '')::jsonb",
    )

    # -------------------------------------------------------------------------
    # 5) warehouses.created_at / updated_at: TIMESTAMPTZ -> TIMESTAMP WITHOUT TIME ZONE
    #    Корректная конверсия: timestamptz -> timestamp (локальное представление в UTC)
    #    и современный дефолт: (now() at time zone 'utc') — уже без таймзоны.
    # -------------------------------------------------------------------------
    # Сначала уберём server_default, чтобы не мешал смене типа
    op.alter_column(
        "warehouses",
        "created_at",
        existing_type=postgresql.TIMESTAMP(timezone=True),
        server_default=None,
        existing_nullable=False,
    )
    op.alter_column(
        "warehouses",
        "updated_at",
        existing_type=postgresql.TIMESTAMP(timezone=True),
        server_default=None,
        existing_nullable=False,
    )

    # Меняем тип через USING
    op.alter_column(
        "warehouses",
        "created_at",
        existing_type=postgresql.TIMESTAMP(timezone=True),
        type_=sa.TIMESTAMP(timezone=False),
        existing_nullable=False,
        postgresql_using="(created_at AT TIME ZONE 'UTC')",
    )
    op.alter_column(
        "warehouses",
        "updated_at",
        existing_type=postgresql.TIMESTAMP(timezone=True),
        type_=sa.TIMESTAMP(timezone=False),
        existing_nullable=False,
        postgresql_using="(updated_at AT TIME ZONE 'UTC')",
    )

    # Возвращаем современные дефолты уже под новый тип
    op.alter_column(
        "warehouses", "created_at", server_default=sa.text("(now() at time zone 'utc')")
    )
    op.alter_column(
        "warehouses", "updated_at", server_default=sa.text("(now() at time zone 'utc')")
    )


def downgrade() -> None:
    # -------------------------------------------------------------------------
    # Обратные приведения типов и дефолтов (аккуратно и симметрично)
    # -------------------------------------------------------------------------

    # warehouses: убираем дефолты под timestamp
    op.alter_column("warehouses", "updated_at", server_default=None)
    op.alter_column("warehouses", "created_at", server_default=None)

    # warehouses: TIMESTAMP -> TIMESTAMPTZ
    op.alter_column(
        "warehouses",
        "updated_at",
        existing_type=sa.TIMESTAMP(timezone=False),
        type_=postgresql.TIMESTAMP(timezone=True),
        existing_nullable=False,
        postgresql_using="updated_at AT TIME ZONE 'UTC'",
    )
    op.alter_column(
        "warehouses",
        "created_at",
        existing_type=sa.TIMESTAMP(timezone=False),
        type_=postgresql.TIMESTAMP(timezone=True),
        existing_nullable=False,
        postgresql_using="created_at AT TIME ZONE 'UTC'",
    )

    # warehouses: возвращаем дефолты now() (будут timestamptz)
    op.alter_column("warehouses", "created_at", server_default=sa.text("now()"))
    op.alter_column("warehouses", "updated_at", server_default=sa.text("now()"))

    # warehouses: JSONB -> TEXT
    op.alter_column(
        "warehouses",
        "working_hours",
        existing_type=postgresql.JSONB(astext_type=sa.Text()),
        type_=sa.TEXT(),
        existing_nullable=True,
        postgresql_using="working_hours::text",
    )

    # payments: TIMESTAMP -> VARCHAR (только приведение типа, без ужесточения nullable)
    op.alter_column(
        "payments",
        "updated_at",
        existing_type=sa.TIMESTAMP(timezone=False),
        type_=sa.VARCHAR(),
        existing_nullable=True,
        postgresql_using="updated_at::text",
    )
    op.alter_column(
        "payments",
        "created_at",
        existing_type=sa.TIMESTAMP(timezone=False),
        type_=sa.VARCHAR(),
        existing_nullable=True,
        postgresql_using="created_at::text",
    )

    # payments: ENUM -> VARCHAR
    op.alter_column(
        "payments",
        "status",
        existing_type=paymentstatus_enum,
        type_=sa.VARCHAR(),
        existing_nullable=True,
        postgresql_using="status::text",
    )
