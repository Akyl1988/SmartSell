"""
Unit tests for Alembic autogenerate table ID filtering.

These are pure unit tests of the filtering logic without database dependencies.
"""

from unittest.mock import MagicMock

import pytest


@pytest.mark.usefixtures()  # Don't apply any automatic fixtures
class TestAlembicAutogenerateFilterLogic:
    """Pure unit tests for table ID filtering logic."""

    def test_include_object_filters_db_only_tables(self):
        """
        Test: include_object filter correctly excludes DB-only reflected tables.

        This is a unit test of the filtering logic without hitting the database.
        """
        from migrations.alembic_autogen_filters import include_object_filter

        # Mock a reflected table object that's not in ORM metadata
        mock_db_only_table = MagicMock()
        mock_db_only_table.schema = None
        mock_db_only_table.name = "some_db_only_table"

        # Simulate: this table is reflected from DB, but not in ORM metadata
        result = include_object_filter(
            object_=mock_db_only_table,
            name="some_db_only_table",
            type_="table",
            reflected=True,
            compare_to=None,  # None = not in ORM metadata
            target_table_ids=set(),  # Empty = nothing in ORM
        )

        # Should be excluded
        assert result is False, "DB-only table (reflected, no compare_to) should be excluded"

    def test_include_object_filters_db_only_indexes(self):
        """
        Test: include_object filter excludes indexes on tables not in ORM metadata.
        """
        from migrations.alembic_autogen_filters import include_object_filter

        # Mock an index on a table that's not in ORM
        mock_db_only_table = MagicMock()
        mock_db_only_table.schema = None
        mock_db_only_table.name = "some_db_only_table"

        mock_index = MagicMock()
        mock_index.table = mock_db_only_table
        mock_index.name = "idx_some_db_only"

        result = include_object_filter(
            object_=mock_index,
            name="idx_some_db_only",
            type_="index",
            reflected=True,
            compare_to=None,
            target_table_ids=set(),  # Empty = nothing in ORM
        )

        # Should be excluded because parent table is not in ORM
        assert result is False, "Index on DB-only table should be excluded"

    def test_include_object_allows_orm_tables(self):
        """
        Test: include_object allows tables that ARE in ORM metadata.
        """
        from migrations.alembic_autogen_filters import include_object_filter

        # Create target_table_ids with a specific table
        schema = "public"
        table_name = "users"
        target_table_ids = {(schema, table_name)}

        # Mock an ORM table
        mock_orm_table = MagicMock()
        mock_orm_table.schema = schema
        mock_orm_table.name = table_name

        result = include_object_filter(
            object_=mock_orm_table,
            name=table_name,
            type_="table",
            reflected=True,
            compare_to=object(),  # non-None = ORM counterpart exists
            target_table_ids=target_table_ids,
        )

        # Should be included because it's in ORM metadata
        assert result is True, f"ORM table {(schema, table_name)} should be included"

    def test_alembic_version_table_always_allowed(self):
        """
        Test: alembic_version table is always allowed regardless of ORM metadata.
        """
        from migrations.alembic_autogen_filters import include_object_filter

        mock_version_table = MagicMock()
        mock_version_table.schema = "public"
        mock_version_table.name = "alembic_version"

        result = include_object_filter(
            object_=mock_version_table,
            name="alembic_version",
            type_="table",
            reflected=True,
            compare_to=None,
            target_table_ids=set(),  # Empty = nothing in ORM, but alembic_version should still be allowed
        )

        # Should always be included
        assert result is True, "alembic_version table should always be allowed"

    def test_reflected_db_only_object_without_compare_to_excluded(self):
        """
        Test: The core rule - any reflected object with no compare_to is excluded.
        """
        from migrations.alembic_autogen_filters import include_object_filter

        # This is the primary protection: reflected=True, compare_to=None
        result = include_object_filter(
            object_=MagicMock(),
            name="any_name",
            type_="trigger",  # Try various types
            reflected=True,
            compare_to=None,
            target_table_ids=set(),
        )

        assert result is False, "Any reflected object with no ORM counterpart should be excluded"

    def test_non_reflected_object_with_no_compare_to_allowed(self):
        """
        Test: Non-reflected objects (ORM-only) are allowed even without compare_to.
        """
        from migrations.alembic_autogen_filters import include_object_filter

        # Mock object must have a schema attribute
        mock_orm_obj = MagicMock()
        mock_orm_obj.schema = None  # schema=None means it's in the default schema

        result = include_object_filter(
            object_=mock_orm_obj,
            name="orm_object",
            type_="table",
            reflected=False,  # Not from database reflection
            compare_to=None,  # No counterpart in DB yet
            target_table_ids={(None, "orm_object")},  # But it's in ORM metadata
        )

        # For tables, needs to be in target_table_ids
        assert result is True, "ORM-declared object should be allowed"

    def test_constraint_on_db_only_table_excluded(self):
        """
        Test: Foreign key constraints on DB-only tables are excluded.
        """
        from migrations.alembic_autogen_filters import include_object_filter

        # Mock a constraint on a table not in ORM
        mock_db_only_table = MagicMock()
        mock_db_only_table.schema = "public"
        mock_db_only_table.name = "db_only_table"

        mock_constraint = MagicMock()
        mock_constraint.table = mock_db_only_table
        mock_constraint.name = "fk_something"

        result = include_object_filter(
            object_=mock_constraint,
            name="fk_something",
            type_="foreign_key_constraint",
            reflected=True,
            compare_to=None,
            target_table_ids=set(),  # db_only_table not in ORM
        )

        assert result is False, "Foreign key constraint on DB-only table should be excluded"

    def test_indexes_on_orm_tables_allowed(self):
        """
        Test: Indexes on ORM tables are allowed.
        """
        from migrations.alembic_autogen_filters import include_object_filter

        # Mock an index on an ORM table
        schema = "public"
        table_name = "products"
        orm_table_ids = {(schema, table_name)}

        mock_orm_table = MagicMock()
        mock_orm_table.schema = schema
        mock_orm_table.name = table_name

        mock_index = MagicMock()
        mock_index.table = mock_orm_table
        mock_index.name = "idx_product_name"

        result = include_object_filter(
            object_=mock_index,
            name="idx_product_name",
            type_="index",
            reflected=True,
            compare_to=object(),
            target_table_ids=orm_table_ids,
        )

        assert result is True, "Index on ORM table should be allowed"
