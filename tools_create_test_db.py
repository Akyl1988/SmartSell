from sqlalchemy import create_engine, text

ADMIN_URL = "postgresql+psycopg2://postgres:admin123@localhost:5432/postgres"
TARGET_DB = "smartselltest2"
engine = create_engine(ADMIN_URL, isolation_level="AUTOCOMMIT")
with engine.connect() as conn:
    exists = conn.execute(
        text("SELECT 1 FROM pg_database WHERE datname = :d"), {"d": TARGET_DB}
    ).scalar()
    if exists:
        print(f"[OK] Database '{TARGET_DB}' already exists")
    else:
        conn.execute(text(f'CREATE DATABASE "{TARGET_DB}" ENCODING ' "UTF8" " TEMPLATE template1"))
        print(f"[OK] Database '{TARGET_DB}' created")
