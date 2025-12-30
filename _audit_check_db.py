from sqlalchemy import text

from app.core.db import engine

with engine.connect() as conn:
    result = conn.execute(
        text(
            """
        SELECT table_name 
        FROM information_schema.tables 
        WHERE table_schema = 'public' 
        ORDER BY table_name
    """
        )
    )
    tables = [r[0] for r in result.fetchall()]
    print("=== ТАБЛИЦЫ В БД smartsell2 ===")
    for t in tables:
        print(f"  - {t}")
    print(f"\nВсего: {len(tables)} таблиц")
