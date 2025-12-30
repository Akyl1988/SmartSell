#!/usr/bin/env python
"""Patch conftest.py to use get_async_db instead of get_db."""
import re

with open("tests/conftest.py", encoding="utf-8") as f:
    content = f.read()

# Replace function
old_func = r'''def _import_app_and_get_db\(\) -> tuple\[Any, Callable\[\.\.\., AsyncIterator\[AsyncSession\]\]\]:
    """
    Возвращает \(app, get_db\)\. Поддерживает оба расположения:
      - app\.core\.db:get_db
      - app\.core\.database:get_db
    """
    # Импорт FastAPI приложения
    try:
        from app\.main import app  # type: ignore
    except Exception as e:
        raise RuntimeError\(f"Cannot import FastAPI app from app\.main: \{e\}"\) from e

    # Импорт get_db
    get_db = None
    # Новый путь
    try:
        from app\.core\.db import get_db as _get_db  # type: ignore

        get_db = _get_db
    except Exception:
        pass
    # Старый путь \(для обратной совместимости\)
    if get_db is None:
        try:
            from app\.core\.database import get_db as _get_db  # type: ignore

            get_db = _get_db
        except Exception as e:
            raise RuntimeError\(
                f"Cannot import get_db from app\.core\.db or app\.core\.database: \{e\}"
            \) from e

    return app, get_db  # type: ignore\[return-value\]'''

new_func = '''def _import_app_and_get_db() -> tuple[Any, Callable[..., AsyncIterator[AsyncSession]]]:
    """Import app and get_async_db from app.core.db or app.core.database."""
    # Импорт FastAPI приложения
    try:
        from app.main import app  # type: ignore
    except Exception as e:
        raise RuntimeError(f"Cannot import FastAPI app from app.main: {e}") from e

    # Import get_async_db
    get_async_db_func = None
    try:
        from app.core.db import get_async_db as _get_async_db  # type: ignore
        get_async_db_func = _get_async_db
    except Exception:
        pass
    if get_async_db_func is None:
        try:
            from app.core.database import get_async_db as _get_async_db  # type: ignore
            get_async_db_func = _get_async_db
        except Exception as e:
            raise RuntimeError(
                f"Cannot import get_async_db from app.core.db or app.core.database: {e}"
            ) from e

    return app, get_async_db_func  # type: ignore[return-value]'''

content_new = re.sub(old_func, new_func, content, flags=re.MULTILINE | re.DOTALL)

if content_new == content:
    print("Pattern not found, doing simple string replace")
    # Fallback: simple replacements
    content_new = (
        content.replace(
            "from app.core.db import get_db as _get_db",
            "from app.core.db import get_async_db as _get_async_db",
        )
        .replace(
            "from app.core.database import get_db as _get_db",
            "from app.core.database import get_async_db as _get_async_db",
        )
        .replace("get_db = _get_db", "get_async_db_func = _get_async_db")
        .replace("get_db = None", "get_async_db_func = None")
        .replace("if get_db is None:", "if get_async_db_func is None:")
        .replace(
            "return app, get_db  # type: ignore[return-value]",
            "return app, get_async_db_func  # type: ignore[return-value]",
        )
        .replace('f"Cannot import get_db from', 'f"Cannot import get_async_db from')
    )

with open("tests/conftest.py", "w", encoding="utf-8") as f:
    f.write(content_new)

print("✅ Patched conftest.py to use get_async_db")
