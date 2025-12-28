import sqlalchemy as sa
from sqlalchemy import text

from tests.conftest import SYNC_TEST_DATABASE_URL


def test_alembic_version_length_is_256(test_db):
    engine = sa.create_engine(SYNC_TEST_DATABASE_URL, future=True)
    try:
        with engine.connect() as conn:
            length = conn.execute(
                text(
                    """
                    select character_maximum_length
                    from information_schema.columns
                    where table_schema='public'
                      and table_name='alembic_version'
                      and column_name='version_num'
                    """
                )
            ).scalar()
        assert length is not None and int(length) >= 256
    finally:
        engine.dispose()
