# migrations/utils/pghelpers.py
"""
Вспомогательные функции для миграций PostgreSQL (Alembic/SQLAlchemy).

- Лаконичный логгер `logger` без внешних зависимостей.
- `ensure_enum_exists` — создать ENUM, если нет; если есть, аккуратно добавить недостающие значения по списку.
- `add_enum_values` — алиас для обратной совместимости (старые миграции).
- `pg_enum` — фабрика типа столбца, которая *не* пытается создавать тип (create_type=False).
- `add_not_null_column_safely` — безопасно добавить NOT NULL-колонку в таблицу с бэкфиллом.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Optional

import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.dialects import postgresql as psql
from sqlalchemy.engine import Connection

# -----------------
# Базовый логгер
# -----------------
logger = logging.getLogger("alembic.migration")
if not logger.handlers:
    _h = logging.StreamHandler()
    _fmt = logging.Formatter("%(levelname)s %(message)s")
    _h.setFormatter(_fmt)
    logger.addHandler(_h)
logger.setLevel(logging.INFO)


# -----------------
# ENUM helpers
# -----------------
def _enum_labels_exist(conn: Connection, schema: str, enum_name: str) -> list[str]:
    q = sa.text(
        """
        SELECT e.enumlabel
        FROM pg_type t
        JOIN pg_enum e ON t.oid = e.enumtypid
        JOIN pg_namespace n ON n.oid = t.typnamespace
        WHERE n.nspname = :schema AND t.typname = :enum_name
        ORDER BY e.enumsortorder
    """
    )
    rows = conn.execute(q, {"schema": schema, "enum_name": enum_name}).fetchall()
    return [r[0] for r in rows]


def ensure_enum_exists(
    conn: Connection,
    enum_name: str,
    labels: Sequence[str],
    schema: str = "public",
) -> None:
    """
    Создаёт ENUM, если он отсутствует. Если тип есть — добавляет недостающие значения.
    Порядок значений сохраняется как в `labels`.
    """
    if not labels:
        return

    exists_q = sa.text(
        """
        SELECT 1
        FROM pg_type t
        JOIN pg_namespace n ON n.oid = t.typnamespace
        WHERE n.nspname = :schema AND t.typname = :enum_name
        LIMIT 1
    """
    )
    exists = conn.execute(exists_q, {"schema": schema, "enum_name": enum_name}).scalar()

    if not exists:
        # создаём тип целиком
        ddl = sa.text(
            f"CREATE TYPE {schema}.{sa.sql.elements.quoted_name(enum_name, quote=True)} "
            f"AS ENUM ({', '.join([sa.literal(v).compile(dialect=conn.dialect) for v in labels])})"
        )
        conn.execute(ddl)
        logger.info("ENUM %s.%s создан.", schema, enum_name)
        return

    # тип существует — добавим недостающие значения
    existing = set(_enum_labels_exist(conn, schema, enum_name))
    prev_label: Optional[str] = None
    for label in labels:
        if label in existing:
            prev_label = label  # двигаем "якорь" для AFTER
            continue
        if prev_label is None:
            # добавим в начало (BEFORE first)
            before = next((l for l in labels if l in existing), None)
            if before:
                conn.execute(
                    text(
                        f"ALTER TYPE {schema}.{sa.sql.elements.quoted_name(enum_name, quote=True)} "
                        f"ADD VALUE {sa.literal(label).compile(dialect=conn.dialect)} BEFORE "
                        f"{sa.literal(before).compile(dialect=conn.dialect)}"
                    )
                )
            else:
                # если почему-то нет существующих ярлыков — обычное ADD VALUE
                conn.execute(
                    text(
                        f"ALTER TYPE {schema}.{sa.sql.elements.quoted_name(enum_name, quote=True)} "
                        f"ADD VALUE {sa.literal(label).compile(dialect=conn.dialect)}"
                    )
                )
        else:
            conn.execute(
                text(
                    f"ALTER TYPE {schema}.{sa.sql.elements.quoted_name(enum_name, quote=True)} "
                    f"ADD VALUE {sa.literal(label).compile(dialect=conn.dialect)} AFTER "
                    f"{sa.literal(prev_label).compile(dialect=conn.dialect)}"
                )
            )
        logger.info("ENUM %s.%s: добавлено значение %s", schema, enum_name, label)
        prev_label = label


def add_enum_values(
    conn: Connection,
    enum_name: str,
    labels: Sequence[str],
    schema: str = "public",
) -> None:
    """Для обратной совместимости: просто вызывает ensure_enum_exists."""
    ensure_enum_exists(conn, enum_name, labels, schema=schema)


def pg_enum(enum_name: str, *labels: str, schema: str = "public") -> psql.ENUM:
    """
    Возвращает postgresql.ENUM, *не создающий* тип автоматически.
    Значения labels здесь игнорируются — порядок/наличие контролируйте через ensure_enum_exists().
    """
    return psql.ENUM(name=enum_name, schema=schema, create_type=False)


# -----------------
# NOT NULL helpers
# -----------------
def add_not_null_column_safely(
    conn: Connection,
    table: str,
    column: sa.Column,
    fill_value_sql_literal: str,
    schema: Optional[str] = None,
) -> None:
    """
    Добавляет NOT NULL-колонку в существующую таблицу безопасно:
      1) ADD COLUMN NULL с DEFAULT (но без фиксации default в каталоге);
      2) backfill через UPDATE (используя переданный литерал SQL);
      3) SET NOT NULL;
      4) убираем DEFAULT.
    Пример:
      add_not_null_column_safely(conn, 'companies',
         sa.Column('settings_version', sa.Integer(), nullable=False),
         fill_value_sql_literal='0')
    """
    sch = f"{schema}." if schema else ""
    colname = column.name
    # 1) ADD COLUMN NULL
    ddl_add = sa.text(
        f"ALTER TABLE {sch}{sa.sql.elements.quoted_name(table, quote=True)} "
        f"ADD COLUMN {sa.sql.elements.quoted_name(colname, quote=True)} {column.type.compile(dialect=conn.dialect)}"
    )
    conn.execute(ddl_add)

    # 2) backfill
    conn.execute(
        sa.text(
            f"UPDATE {sch}{sa.sql.elements.quoted_name(table, quote=True)} "
            f"SET {sa.sql.elements.quoted_name(colname, quote=True)} = {fill_value_sql_literal} "
            f"WHERE {sa.sql.elements.quoted_name(colname, quote=True)} IS NULL"
        )
    )

    # 3) SET NOT NULL
    conn.execute(
        sa.text(
            f"ALTER TABLE {sch}{sa.sql.elements.quoted_name(table, quote=True)} "
            f"ALTER COLUMN {sa.sql.elements.quoted_name(colname, quote=True)} SET NOT NULL"
        )
    )

    # 4) Убираем DEFAULT, если он случайно был навешен автогенератором (наша функция его не ставит)
    try:
        conn.execute(
            sa.text(
                f"ALTER TABLE {sch}{sa.sql.elements.quoted_name(table, quote=True)} "
                f"ALTER COLUMN {sa.sql.elements.quoted_name(colname, quote=True)} DROP DEFAULT"
            )
        )
    except Exception:
        pass
