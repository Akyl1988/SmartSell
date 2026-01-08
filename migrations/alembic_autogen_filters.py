"""
Testable helpers for Alembic autogenerate filtering.

These functions are extracted for testability without requiring full Alembic context.
"""

from typing import Any


def build_target_table_ids(target_metadata) -> set[tuple[str | None, str]]:
    """
    Extract target table IDs from ORM metadata.

    Args:
        target_metadata: SQLAlchemy MetaData instance with ORM tables

    Returns:
        Set of (schema, tablename) tuples for all tables in metadata
    """
    table_ids: set[tuple[str | None, str]] = set()
    if target_metadata:
        for table in target_metadata.tables.values():
            table_ids.add((table.schema, table.name))
    return table_ids


def include_object_filter(
    object_: Any,
    name: str,
    type_: str,
    reflected: bool,
    compare_to: Any,
    target_table_ids: set[tuple[str | None, str]],
    alembic_version_table: str = "alembic_version",
) -> bool:
    """
    Core Alembic autogenerate filter logic for table ID validation.

    Rules:
    - reflected DB-only objects (compare_to=None) are excluded
    - Tables: Allow alembic_version; otherwise only include if in target_table_ids
    - Indexes/constraints: Only include if parent table is in target_table_ids
    - Everything else: Include by default

    Args:
        object_: The SQLAlchemy object being filtered
        name: Object name
        type_: Object type ('table', 'index', etc.)
        reflected: True if object came from DB reflection
        compare_to: ORM metadata counterpart (None = no ORM match)
        target_table_ids: Set of (schema, tablename) tuples from ORM
        alembic_version_table: Name of Alembic version table to always allow

    Returns:
        bool: True to include the object, False to exclude
    """
    # Special case: always allow alembic_version table
    if type_ == "table" and name == alembic_version_table:
        return True

    # Core rule: reflected DB-only objects (no ORM counterpart) should be excluded
    if reflected and compare_to is None:
        return False

    if type_ == "table":
        # For all other tables, only include if they're in ORM metadata
        table_id = (object_.schema if hasattr(object_, "schema") else None, name)
        if table_id not in target_table_ids:
            return False
        return True

    # For indexes and constraints, check parent table
    if type_ in ("index", "unique_constraint", "foreign_key_constraint", "check_constraint"):
        # Get parent table info
        parent_table = getattr(object_, "table", None)
        if parent_table is not None:
            parent_table_id = (parent_table.schema, parent_table.name)
            if parent_table_id not in target_table_ids:
                return False
        return True

    # All other types included by default
    return True
