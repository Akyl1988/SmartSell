from sqlalchemy import create_engine, inspect
from app.models import Base  # важно: app/models/__init__.py должен импортировать все модели
import traceback


def print_header(title: str):
    print("\n" + "=" * 20 + f" {title} " + "=" * 20)


def main():
    engine = create_engine("sqlite:///:memory:", echo=False)

    # Пытаемся создать все таблицы — если есть проблема с FK/маппером, увидим её явно
    try:
        Base.metadata.create_all(engine)
    except Exception as e:
        print_header("CREATE_ALL FAILED")
        traceback.print_exc()

    print_header("TABLES IN METADATA")
    for name in Base.metadata.tables.keys():
        print("-", name)

    insp = inspect(engine)

    print_header("FOREIGN KEYS (by table)")
    for tname, table in Base.metadata.tables.items():
        if not table.foreign_key_constraints:
            continue
        for fk in table.foreign_key_constraints:
            cols = ", ".join(c.name for c in fk.columns)
            # fk.referred_table может быть None, если конфигурация не удалась
            ref = fk.referred_table.fullname if fk.referred_table is not None else "<unresolved>"
            print(f"{tname}.{cols} -> {ref}")

    print_header("ORM MAPPERS")
    try:
        for m in Base.registry.mappers:
            tbl = m.local_table.name if m.local_table is not None else "NO_TABLE"
            print("-", m.class_.__name__, "->", tbl)
    except Exception:
        traceback.print_exc()


if __name__ == "__main__":
    main()
