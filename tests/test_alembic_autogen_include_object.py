"""
Unit tests for app.core.alembic_autogen.include_object filter.

Tests the core DB-only object filtering logic for Alembic autogenerate.
"""

import pytest

from app.core.alembic_autogen import include_object


class TestIncludeObject:
    """Tests for the include_object autogenerate filter."""

    def test_reflected_table_with_no_compare_to_excluded(self):
        """
        Test: reflected table with no ORM counterpart (compare_to=None) is excluded.

        This is the core protection against suggesting DROP for DB-only tables.
        """
        result = include_object(
            obj=None,  # obj param is not used in the filter logic
            name="some_table",
            type_="table",
            reflected=True,
            compare_to=None,
        )
        assert result is False

    def test_reflected_index_with_no_compare_to_excluded(self):
        """
        Test: reflected index with no ORM counterpart is excluded.
        """
        result = include_object(
            obj=None,
            name="some_index",
            type_="index",
            reflected=True,
            compare_to=None,
        )
        assert result is False

    def test_non_reflected_object_with_no_compare_to_included(self):
        """
        Test: non-reflected (ORM-declared) object without compare_to is included.

        This handles ORM-only objects that haven't been reflected yet.
        """
        result = include_object(
            obj=None,
            name="some_table",
            type_="table",
            reflected=False,
            compare_to=None,
        )
        assert result is True

    def test_reflected_object_with_compare_to_included(self):
        """
        Test: reflected object with matching ORM counterpart is included.

        This means the object exists in both DB and ORM metadata.
        """
        result = include_object(
            obj=None,
            name="some_table",
            type_="table",
            reflected=True,
            compare_to=object(),  # non-None compare_to indicates ORM counterpart exists
        )
        assert result is True

    def test_non_reflected_with_compare_to_included(self):
        """
        Test: ORM object with matching reflected DB object is included.
        """
        result = include_object(
            obj=None,
            name="some_table",
            type_="table",
            reflected=False,
            compare_to=object(),
        )
        assert result is True

    def test_various_object_types_with_reflected_no_compare_excluded(self):
        """
        Test: various object types are excluded when reflected without compare_to.
        """
        types_to_test = ["table", "index", "column", "constraint", "trigger"]

        for obj_type in types_to_test:
            result = include_object(
                obj=None,
                name=f"some_{obj_type}",
                type_=obj_type,
                reflected=True,
                compare_to=None,
            )
            assert result is False, f"Failed for type: {obj_type}"

    def test_various_object_types_non_reflected_included(self):
        """
        Test: various ORM object types are included (no DB reflection).
        """
        types_to_test = ["table", "index", "column", "constraint", "trigger"]

        for obj_type in types_to_test:
            result = include_object(
                obj=None,
                name=f"some_{obj_type}",
                type_=obj_type,
                reflected=False,
                compare_to=None,
            )
            assert result is True, f"Failed for type: {obj_type}"
