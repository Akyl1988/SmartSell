# tests/conftest.py
"""
Pytest configuration and fixtures for async database testing.

Ключевые особенности:
- PostgreSQL (asyncpg) как единственный источник истины для интеграционных тестов.
- Для Postgres: перед create_all лениво подгружаем весь домен (import_*_models) — один раз.
- Для SQLite: глобальный безопасный create_all — создаём только самодостаточные таблицы (без «висячих» FK)
  и только с типами, поддерживаемыми SQLite (JSONB/ARRAY/INET/... пропускаем).
- Лёгкая автозагрузка ключевых моделей (Company/User/Warehouse/AuditLog), чтобы строковые relationship(...)
  резолвились и не падали мапперы.
- Удобные фикстуры клиента (sync/async), сессий, сброса данных и фабрик доменных сущностей.
- Явное DATABASE_URL (psycopg2) для прохождения test_database_url_default.
- Дружественная обработка env: TEST_ASYNC_DATABASE_URL (предпочтительно) или fallback к TEST_DATABASE_URL,
  включая автоконверсию драйвера psycopg2 -> asyncpg при необходимости (только для тестового async engine).
- Патч синхронного SQLAlchemy create_engine для совместимости с NullPool: вырезаем pool_* kwargs,
  чтобы /api/auth/register не падал в тестах, которые создают клиент без DI-оверрайдов.
- Патч TestClient: его HTTP-методы можно безопасно await-ить в async-тестах.

Во всём модуле — UTF-8.
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from datetime import timedelta
from typing import Any
from urllib.parse import quote

# Guard against pytest-xdist usage (not supported in this repo)
if os.environ.get("PYTEST_XDIST_WORKER") or os.environ.get("XDIST_WORKER"):
    raise RuntimeError(
        "pytest-xdist is not supported in this repository. " "Please run tests with: python -m pytest -q"
    )

import httpx
import pytest
import pytest_asyncio
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from httpx import AsyncClient
from sqlalchemy import MetaData, Table, text
from sqlalchemy import exc as sa_exc  # РѕР±СЂР°Р±РѕС‚РєР° OperationalError/В«already existsВ»
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.engine.url import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool
from sqlalchemy.sql.type_api import TypeEngine  # С‚РёРїС‹ РґР»СЏ С„РёР»СЊС‚СЂР° unsupported

# Compatibility shim: newer trio may not expose MultiError; avoid touching deprecated attribute when present.
try:
    import trio  # type: ignore

    if "MultiError" not in getattr(trio, "__dict__", {}):

        class _TrioMultiError(BaseExceptionGroup):  # type: ignore[misc]
            ...

        trio.MultiError = _TrioMultiError  # type: ignore[attr-defined]
except Exception:
    pass

# ======================================================================================
# 0) Р‘СѓС‚СЃС‚СЂР°Рї РѕРєСЂСѓР¶РµРЅРёСЏ
# ======================================================================================

# Р’СЃСЋРґСѓ UTF-8 (С†РµРЅС‚СЂР°Р»РёР·РѕРІР°РЅРЅРѕ)
os.environ.setdefault("PYTHONIOENCODING", "UTF-8")

# Disable rate limiting in tests by default (can be overridden explicitly)
os.environ.setdefault("RATE_LIMIT_ENABLED", "0")
os.environ.setdefault("TESTING", "1")
os.environ.setdefault("FORCE_INMEMORY_BACKENDS", "1")
os.environ.setdefault("TEST_REDIS_DISABLED", "1")

# РЇРІРЅРѕ СѓРєР°Р¶РµРј "СЃРѕРІСЂРµРјРµРЅРЅС‹Р№" strict-СЂРµР¶РёРј asyncio, РµСЃР»Рё РїР»Р°РіРёРЅ РЅРµ РґРµР»Р°РµС‚ СЌС‚РѕРіРѕ СЃР°Рј.
os.environ.setdefault("PYTEST_ASYNCIO_MODE", "strict")


# ======================================================================================
# 0.1) РџР°С‚С‡: Р±РµР·РѕРїР°СЃРЅС‹Р№ sync create_engine РґР»СЏ NullPool
# ======================================================================================
# Р’ prod-РєРѕРґРµ РїСЂРё СЃРѕР·РґР°РЅРёРё sync Engine (psycopg2) РјРѕР¶РµС‚ РёСЃРїРѕР»СЊР·РѕРІР°С‚СЊСЃСЏ NullPool,
# РЅРѕ РїСЂРё СЌС‚РѕРј РїРµСЂРµРґР°РІР°С‚СЊСЃСЏ pool_size/max_overflow/pool_timeout вЂ” С‡С‚Рѕ РЅРµРґРѕРїСѓСЃС‚РёРјРѕ.
# Р­С‚РѕС‚ РїР°С‚С‡ С„РёР»СЊС‚СЂСѓРµС‚ С‚Р°РєРёРµ kwargs С‚РѕР»СЊРєРѕ РґР»СЏ sync create_engine Рё С‚РѕР»СЊРєРѕ РґР»СЏ NullPool.
_original_create_engine = sa.create_engine


def _patched_create_engine(*args, **kwargs):
    try:
        poolclass = kwargs.get("poolclass")
        # РЇРІРЅС‹Р№ NullPool РёР»Рё РїРѕРґРєР»Р°СЃСЃ вЂ” РІС‹СЂРµР·Р°РµРј pool_* РїР°СЂР°РјРµС‚СЂС‹, СЃ РєРѕС‚РѕСЂС‹РјРё NullPool РЅРµ СЃРѕРІРјРµСЃС‚РёРј
        if poolclass is NullPool or (isinstance(poolclass, type) and issubclass(poolclass, NullPool)):
            for bad in (
                "pool_size",
                "max_overflow",
                "pool_timeout",
                "pool_recycle",
                "pool_use_lifo",
            ):
                kwargs.pop(bad, None)
    except Exception:
        # Р‘РµР·РѕРїР°СЃРЅРѕ РёРіРЅРѕСЂРёСЂСѓРµРј вЂ” РїСѓСЃС‚СЊ СѓРїР°РґС‘С‚ РІ РѕСЂРёРіРёРЅР°Р»СЊРЅРѕРј create_engine, РµСЃР»Рё С‡С‚Рѕ-С‚Рѕ РёРЅРѕРµ
        pass
    return _original_create_engine(*args, **kwargs)


# РџСЂРёРјРµРЅСЏРµРј РїР°С‚С‡ СЃРёРЅС…СЂРѕРЅРЅРѕРіРѕ create_engine РЎР РђР—РЈ (РґРѕ РёРјРїРѕСЂС‚РѕРІ РїСЂРёР»РѕР¶РµРЅРёСЏ)
sa.create_engine = _patched_create_engine  # type: ignore[assignment]


# ======================================================================================
# 0.2) РџР°С‚С‡ TestClient: СЂР°Р·СЂРµС€РёС‚СЊ await client.get(...)
# ======================================================================================


class _AwaitableResponse:
    """РћР±С‘СЂС‚РєР°, РєРѕС‚РѕСЂР°СЏ РІС‹РіР»СЏРґРёС‚ РєР°Рє Response Рё РјРѕР¶РµС‚ Р±С‹С‚СЊ await-РЅСѓС‚Р° (РІРѕР·РІСЂР°С‰Р°СЏ Response)."""

    __slots__ = ("_resp",)

    def __init__(self, resp: Any) -> None:
        self._resp = resp

    def __getattr__(self, name: str) -> Any:
        return getattr(self._resp, name)

    def __repr__(self) -> str:  # pragma: no cover
        return f"_AwaitableResponse({self._resp!r})"

    def __await__(self):
        async def _return_resp():
            return self._resp

        return _return_resp().__await__()


def _wrap_tc_method(method: Callable[..., Any]) -> Callable[..., _AwaitableResponse]:
    def _wrapped(*args, **kwargs) -> _AwaitableResponse:
        resp = method(*args, **kwargs)
        return _AwaitableResponse(resp)

    _wrapped.__await_shim__ = True  # type: ignore[attr-defined]
    _wrapped.__name__ = getattr(method, "__name__", "wrapped")
    _wrapped.__doc__ = getattr(method, "__doc__", None)
    return _wrapped


def _patch_testclient_async_await() -> None:
    methods = ("get", "post", "put", "patch", "delete", "options", "head", "request")
    for name in methods:
        if not hasattr(TestClient, name):
            continue
        orig = getattr(TestClient, name)
        if getattr(orig, "__await_shim__", False):  # РЈР¶Рµ РїР°С‚С‡РµРЅРѕ?
            continue
        setattr(TestClient, name, _wrap_tc_method(orig))


_patch_testclient_async_await()


# ======================================================================================
# Р’РЎРџРћРњРћР“РђРўР•Р›Р¬РќР«Р• РЈРўРР›РРўР«: URLвЂ™С‹, РёРјРїРѕСЂС‚ app Рё РјРѕРґРµР»РµР№, РїРѕРёСЃРє get_db
# ======================================================================================


def _get_async_test_url() -> str:
    """
    Р’РѕР·РІСЂР°С‰Р°РµС‚ async URL РґР»СЏ С‚РµСЃС‚РѕРІРѕРіРѕ РґРІРёР¶РєР°:
      1) TEST_ASYNC_DATABASE_URL (РµСЃР»Рё Р·Р°РґР°РЅ)
      2) РёРЅР°С‡Рµ TEST_DATABASE_URL вЂ” РїСЂРё РЅРµРѕР±С…РѕРґРёРјРѕСЃС‚Рё РєРѕРЅРІРµСЂС‚РёСЂСѓРµРј РґСЂР°Р№РІРµСЂ psycopg2 -> asyncpg
      3) РёРЅР°С‡Рµ СЃРѕР±РёСЂР°РµРј РёР· TEST_DB_* (С‚СЂРµР±СѓРµС‚ РїР°СЂРѕР»СЊ, Р±РµР· РґРµС„РѕР»С‚РѕРІ)
    """

    def _build_from_parts() -> str:
        user = os.getenv("TEST_DB_USER") or os.getenv("POSTGRES_USER")
        password = os.getenv("TEST_DB_PASSWORD") or os.getenv("POSTGRES_PASSWORD")
        host = os.getenv("TEST_DB_HOST") or "127.0.0.1"
        port = os.getenv("TEST_DB_PORT") or "5432"
        dbname = os.getenv("TEST_DB_NAME") or os.getenv("POSTGRES_DB") or "smartsell_test"

        if not user:
            raise RuntimeError("TEST_DB_USER (or POSTGRES_USER) is required when TEST_DATABASE_URL is missing")
        if not password:
            raise RuntimeError("TEST_DB_PASSWORD (or POSTGRES_PASSWORD) is required when TEST_DATABASE_URL is missing")

        return f"postgresql+asyncpg://{quote(user, safe='')}:{quote(password, safe='')}" f"@{host}:{port}/{dbname}"

    async_url = os.getenv("TEST_ASYNC_DATABASE_URL")
    base_url = os.getenv("TEST_DATABASE_URL")

    if async_url:
        url = async_url
    elif base_url:
        if base_url.startswith("postgresql+psycopg2://"):
            url = "postgresql+asyncpg://" + base_url.split("postgresql+psycopg2://", 1)[1]
        elif base_url.startswith("postgresql://"):
            url = "postgresql+asyncpg://" + base_url.split("postgresql://", 1)[1]
        elif base_url.startswith("postgres://"):
            url = "postgresql+asyncpg://" + base_url.split("postgres://", 1)[1]
        else:
            url = base_url
    else:
        url = _build_from_parts()

    if not url.startswith("postgresql+"):
        raise RuntimeError(
            f"Expected a PostgreSQL URL for tests, got '{url}'. "
            f"Use TEST_ASYNC_DATABASE_URL='postgresql+asyncpg://...'"
        )
    if not url.startswith("postgresql+asyncpg://"):
        raise RuntimeError(
            f"Async engine requires async driver. Got '{url}'. "
            f"Use TEST_ASYNC_DATABASE_URL (postgresql+asyncpg://...) "
            f"or set TEST_DATABASE_URL with asyncpg driver."
        )
    return url


def _make_sync_test_url(async_url: str) -> str:
    """Конвертируем asyncpg DSN в sync (psycopg2) для Alembic/psycopg2 задач."""
    if async_url.startswith("postgresql+asyncpg://"):
        return "postgresql+psycopg2://" + async_url.split("postgresql+asyncpg://", 1)[1]
    if async_url.startswith("postgresql+psycopg2://"):
        return async_url
    if async_url.startswith("postgresql://"):
        return async_url
    return async_url.replace("postgresql+asyncpg", "postgresql+psycopg2")


def _ensure_test_urls() -> tuple[str, str]:
    """Ensure consistent test URLs with password and export them to env."""
    async_url = _get_async_test_url()
    sync_url = _make_sync_test_url(async_url)

    os.environ["TEST_ASYNC_DATABASE_URL"] = async_url
    os.environ["TEST_DATABASE_URL"] = sync_url
    os.environ["DATABASE_URL"] = sync_url
    os.environ.setdefault("DB_URL", sync_url)

    try:
        pw = make_url(async_url).password or ""
    except Exception:
        pw = ""
    if pw and not os.getenv("PGPASSWORD"):
        os.environ["PGPASSWORD"] = pw

    return async_url, sync_url


def _import_app_and_get_db() -> tuple[Any, Callable[..., AsyncIterator[AsyncSession]]]:
    """
    Р’РѕР·РІСЂР°С‰Р°РµС‚ (app, get_db). РџРѕРґРґРµСЂР¶РёРІР°РµС‚ РѕР±Р° СЂР°СЃРїРѕР»РѕР¶РµРЅРёСЏ:
      - app.core.db:get_db
      - app.core.database:get_db
    """
    # РРјРїРѕСЂС‚ FastAPI РїСЂРёР»РѕР¶РµРЅРёСЏ
    try:
        from app.main import app  # type: ignore
    except Exception as e:
        raise RuntimeError(f"Cannot import FastAPI app from app.main: {e}") from e

    # РРјРїРѕСЂС‚ get_db
    get_async_db_func = None
    # РќРѕРІС‹Р№ РїСѓС‚СЊ
    try:
        from app.core.db import get_async_db as _get_async_db  # type: ignore

        get_async_db_func = _get_async_db
    except Exception:
        pass
    # РЎС‚Р°СЂС‹Р№ РїСѓС‚СЊ (РґР»СЏ РѕР±СЂР°С‚РЅРѕР№ СЃРѕРІРјРµСЃС‚РёРјРѕСЃС‚Рё)
    if get_async_db_func is None:
        try:
            from app.core.database import get_async_db as _get_async_db  # type: ignore

            get_async_db_func = _get_async_db
        except Exception as e:
            raise RuntimeError(f"Cannot import get_async_db from app.core.db or app.core.database: {e}") from e

    return app, get_async_db_func  # type: ignore[return-value]


def _find_sync_get_db_funcs() -> list[Callable[..., AsyncIterator[AsyncSession]]]:
    funcs: list[Callable[..., AsyncIterator[AsyncSession]]] = []
    try:
        from app.core.db import get_db as _sync_get_db  # type: ignore

        funcs.append(_sync_get_db)
    except Exception:
        pass
    try:
        from app.core.database import get_db as _sync_get_db_old  # type: ignore

        if _sync_get_db_old not in funcs:
            funcs.append(_sync_get_db_old)
    except Exception:
        pass
    return funcs


def _import_all_models_once() -> bool:
    """
    РџС‹С‚Р°РµРјСЃСЏ РІС‹Р·РІР°С‚СЊ РѕРґРёРЅ РёР· С…РµР»РїРµСЂРѕРІ РІ app.models:
    - import_models_once (РїСЂРµРґРїРѕС‡С‚РёС‚РµР»СЊРЅРѕ, СЃРѕРІРјРµСЃС‚РёРјРѕ СЃ РЅРѕРІС‹Рј __init__.py)
    - import_all_models (СЃС‚Р°СЂРѕРµ РЅР°Р·РІР°РЅРёРµ)
    - import_domain_models (fallback)
    Р’РѕР·РІСЂР°С‰Р°РµС‚ True, РµСЃР»Рё РёРјРїРѕСЂС‚РёСЂРѕРІР°Р»Рё РґРѕРјРµРЅ; РёРЅР°С‡Рµ False.
    """
    try:
        import app.models as m  # type: ignore

        if hasattr(m, "import_models_once"):
            m.import_models_once()  # type: ignore[attr-defined]
            return True
        if hasattr(m, "import_all_models"):
            m.import_all_models()  # type: ignore[attr-defined]
            return True
        if hasattr(m, "import_domain_models"):
            m.import_domain_models([])  # type: ignore[attr-defined]
            return True
    except Exception:
        pass
    return False


def _bootstrap_minimal_models() -> None:
    """
    Р›С‘РіРєР°СЏ Р°РІС‚РѕР·Р°РіСЂСѓР·РєР° С‚РѕР»СЊРєРѕ РєСЂРёС‚РёС‡РЅС‹С… РєР»Р°СЃСЃРѕРІ, С‡С‚РѕР±С‹ СЃС‚СЂРѕРєРѕРІС‹Рµ relationship(...)
    РЅРµ РїР°РґР°Р»Рё РїСЂРё РєРѕРЅС„РёРіСѓСЂР°С†РёРё РјР°РїРїРµСЂРѕРІ РІ SQLite-СЋРЅРёС‚Р°С….
    """
    try:
        import app.models.company  # type: ignore  # СЂРµРіРёСЃС‚СЂРёСЂСѓРµС‚ Company/companies
    except Exception:
        pass
    try:
        import app.models.user  # type: ignore
    except Exception:
        pass
    # С‡С‚РѕР±С‹ relationship РїРѕ СЃРєР»Р°РґР°Рј Рё Р°СѓРґРёС‚Сѓ РєРѕРЅС„РёРіСѓСЂРёСЂРѕРІР°Р»РёСЃСЊ (СѓР±РёСЂР°РµРј NoForeignKeysError)
    try:
        import app.models.warehouse  # type: ignore
    except Exception:
        pass
    try:
        import app.models.audit_log  # type: ignore
    except Exception:
        pass


# ======================================================================================
# 1) РќР°СЃС‚СЂРѕР№РєР° С‚РµСЃС‚РѕРІРѕРіРѕ AsyncEngine (PostgreSQL + asyncpg)
# ======================================================================================

TEST_DATABASE_URL, SYNC_TEST_DATABASE_URL = _ensure_test_urls()

# Module-level engine/sessionmaker variables initialized to None
# Created AFTER alembic upgrade in test_db fixture
_async_test_engine: AsyncEngine | None = None
TestingSessionLocal: async_sessionmaker[AsyncSession] | None = None
sync_engine: Engine | None = None
logger = logging.getLogger(__name__)
_TEST_RUN_INTERRUPTED = False


def pytest_keyboard_interrupt(excinfo):
    """Mark test session as interrupted to skip heavy teardown on Ctrl+C."""
    global _TEST_RUN_INTERRUPTED
    _TEST_RUN_INTERRUPTED = True


def _dispose_test_engines() -> None:
    """Best-effort disposal of async/sync engines; never raises."""
    global _async_test_engine, sync_engine, TestingSessionLocal

    try:
        if _async_test_engine is not None:
            try:
                await_future = _async_test_engine.dispose()
                if hasattr(await_future, "__await__"):
                    asyncio.run(await_future)
            except Exception as e:  # noqa: PERF203 — log for diagnosis
                logger.warning("Failed to dispose async test engine: %s", e)
            _async_test_engine = None
    except Exception as e:  # noqa: PERF203 — log for diagnosis
        logger.warning("Async engine cleanup error: %s", e)

    try:
        if sync_engine is not None:
            try:
                sync_engine.dispose()
            except Exception as e:  # noqa: PERF203 — log for diagnosis
                logger.warning("Failed to dispose sync test engine: %s", e)
            sync_engine = None
    except Exception as e:  # noqa: PERF203 — log for diagnosis
        logger.warning("Sync engine cleanup error: %s", e)

    TestingSessionLocal = None


# ======================================================================================
# 2) Р“Р»РѕР±Р°Р»СЊРЅР°СЏ Р·Р°С‰РёС‚Р° create_all РґР»СЏ SQLite + РјРёРЅРёРјР°Р»СЊРЅР°СЏ Р°РІС‚РѕР·Р°РіСЂСѓР·РєР° РєР»Р°СЃСЃРѕРІ
# ======================================================================================


def _is_sqlite_bind(bind: Any) -> bool:
    """РћРїСЂРµРґРµР»СЏРµРј, С‡С‚Рѕ create_all РІС‹Р·С‹РІР°СЋС‚ РґР»СЏ SQLite (РѕСЃРѕР±РµРЅРЅРѕ :memory:)."""
    try:
        if isinstance(bind, Engine | Connection):
            return getattr(bind.dialect, "name", "") == "sqlite"
    except Exception:
        pass
    return False


def _sqlite_extract_target_table_names(t: Table) -> set[str]:
    """Р’РµСЂРЅС‘С‚ РјРЅРѕР¶РµСЃС‚РІРѕ РёРјС‘РЅ С‚Р°Р±Р»РёС†, РЅР° РєРѕС‚РѕСЂС‹Рµ СЃРјРѕС‚СЂСЏС‚ FK СЌС‚РѕР№ С‚Р°Р±Р»РёС†С‹ (РїРѕ 'target_fullname' / '_colspec')."""
    targets: set[str] = set()
    for fk in t.foreign_keys:
        spec = getattr(fk, "target_fullname", None) or getattr(fk, "_colspec", None)
        if spec:
            table_name = str(spec).split(".", 1)[0]
            if table_name:
                targets.add(table_name)
    return targets


# С„РёР»СЊС‚СЂ РЅРµРїРѕРґРґРµСЂР¶РёРІР°РµРјС‹С… SQLite С‚РёРїРѕРІ (JSONB Рё РґСЂ.)
_POSTGRES_ONLY_TYPENAMES = {
    "JSONB",
    "ARRAY",
    "HSTORE",
    "CIDR",
    "INET",
    "UUID",  # РїРѕ СѓРјРѕР»С‡Р°РЅРёСЋ РїСЂРѕРїСѓСЃРєР°РµРј; РµСЃР»Рё РµСЃС‚СЊ РєР°СЃС‚РѕРјРЅС‹Р№ С‚РёРї вЂ” РјРѕР¶РЅРѕ СѓР±СЂР°С‚СЊ РёР· СЃРїРёСЃРєР°
}


def _sqlite_is_supported_type(typ: TypeEngine) -> bool:
    """
    Р’РѕР·РІСЂР°С‰Р°РµС‚ False РґР»СЏ С‚РёРїРѕРІ, РєРѕС‚РѕСЂС‹Рµ SQLite РЅРµ СѓРјРµРµС‚ РєРѕРјРїРёР»РёСЂРѕРІР°С‚СЊ РЅР°С‚РёРІРЅРѕ.
    РџСЂРѕРІРµСЂСЏРµРј РїРѕ РёРјРµРЅРё РєР»Р°СЃСЃР° С‚РёРїР° (РЅР°РїСЂРёРјРµСЂ, JSONB, ARRAY Рё С‚.Рї.).
    """
    try:
        tname = type(typ).__name__.upper()
    except Exception:
        return False
    if tname in _POSTGRES_ONLY_TYPENAMES:
        return False
    # РїРѕРґСЃС‚СЂР°С…СѓРµРјСЃСЏ РѕС‚ СЂР°Р·РЅС‹С… РґРёР°Р»РµРєС‚РЅС‹С… РѕР±С‘СЂС‚РѕРє
    if "JSONB" in tname:
        return False
    return True


def _sqlite_is_supported_table(t: Table) -> bool:
    """РўР°Р±Р»РёС†Р° РїРѕРґРґРµСЂР¶РёРІР°РµС‚СЃСЏ SQLite, РµСЃР»Рё РІСЃРµ РµС‘ СЃС‚РѕР»Р±С†С‹ вЂ” СЃ РїРѕРґРґРµСЂР¶РёРІР°РµРјС‹РјРё С‚РёРїР°РјРё."""
    try:
        for c in t.columns:
            if not _sqlite_is_supported_type(c.type):
                return False
        return True
    except Exception:
        # РµСЃР»Рё РЅРµ СЃРјРѕРіР»Рё РїСЂРѕС‡РёС‚Р°С‚СЊ С‚РёРїС‹, Р»СѓС‡С€Рµ РЅРµ СЃРѕР·РґР°РІР°С‚СЊ С‚Р°РєСѓСЋ С‚Р°Р±Р»РёС†Сѓ
        return False


def _sqlite_self_contained_tables(md: MetaData) -> list[Table]:
    """
    РўР°Р±Р»РёС†С‹, Сѓ РєРѕС‚РѕСЂС‹С… РІСЃРµ FK СѓРєР°Р·С‹РІР°СЋС‚ РЅР° СЂРµР°Р»СЊРЅРѕ РїСЂРёСЃСѓС‚СЃС‚РІСѓСЋС‰РёРµ РІ С‚РµРєСѓС‰РµРј MetaData
    Р С‚Р°Р±Р»РёС†Р° РЅРµ СЃРѕРґРµСЂР¶РёС‚ Р·Р°РІРµРґРѕРјРѕ РЅРµРїРѕРґРґРµСЂР¶РёРІР°РµРјС‹С… РґР»СЏ SQLite С‚РёРїРѕРІ.
    """
    present: set[str] = set(md.tables.keys())
    result: list[Table] = []
    for t in md.tables.values():
        if _sqlite_extract_target_table_names(t).issubset(present) and _sqlite_is_supported_table(t):
            result.append(t)
    return result


@pytest.fixture(scope="session", autouse=True)
def _sqlite_safe_create_all_monkeypatch() -> Iterator[None]:
    """
    Р“Р»РѕР±Р°Р»СЊРЅРѕ РјРѕРЅРёРїР°С‚С‡РёРј MetaData.create_all РґР»СЏ SQLite РЅР° РІСЂРµРјСЏ С‚РµСЃС‚РѕРІРѕР№ СЃРµСЃСЃРёРё.
    РЎРѕР·РґР°С‘Рј С‚РѕР»СЊРєРѕ В«СЃР°РјРѕРґРѕСЃС‚Р°С‚РѕС‡РЅС‹РµВ» С‚Р°Р±Р»РёС†С‹, С‡С‚РѕР±С‹ РёР·Р±РµР¶Р°С‚СЊ NoReferencedTableError,
    Рё РїСЂРѕРїСѓСЃРєР°РµРј С‚Р°Р±Р»РёС†С‹ СЃ РЅРµРїРѕРґРґРµСЂР¶РёРІР°РµРјС‹РјРё С‚РёРїР°РјРё (РЅР°РїСЂРёРјРµСЂ, JSONB).
    РўР°РєР¶Рµ Р°РєРєСѓСЂР°С‚РЅРѕ РёРіРЅРѕСЂРёСЂСѓРµРј В«index ... already existsВ» РІ SQLite.
    """
    original_create_all = MetaData.create_all

    def _safe_create_all(self: MetaData, bind: Any = None, **kwargs):
        if _is_sqlite_bind(bind):
            from sqlalchemy import MetaData as _MD

            tables = _sqlite_self_contained_tables(self)
            if not tables:
                return
            tmp = _MD()
            for t in tables:
                # СЃРѕРІСЂРµРјРµРЅРЅС‹Р№ РјРµС‚РѕРґ (РІРјРµСЃС‚Рѕ t.tometadata)
                t.to_metadata(tmp)

            # РіР°СЂР°РЅС‚РёСЂСѓРµРј checkfirst=True Рё РёРіРЅРѕСЂРёСЂСѓРµРј В«index already existsВ»
            kwargs.setdefault("checkfirst", True)
            try:
                return original_create_all(tmp, bind=bind, **kwargs)
            except sa_exc.OperationalError as e:
                msg = (str(e) or "").lower()
                if "already exists" in msg and "index" in msg:
                    # Р±РµР·РѕРїР°СЃРЅРѕ РїСЂРѕРїСѓСЃРєР°РµРј РїРѕРІС‚РѕСЂРЅРѕРµ СЃРѕР·РґР°РЅРёРµ РёРЅРґРµРєСЃР°
                    return None
                raise
        # РЅРµ SQLite вЂ” РѕР±С‹С‡РЅРѕРµ РїРѕРІРµРґРµРЅРёРµ
        kwargs.setdefault("checkfirst", True)
        try:
            return original_create_all(self, bind=bind, **kwargs)
        except sa_exc.OperationalError as e:
            # РїРѕРґСЃС‚СЂР°С…РѕРІРєР°: РµСЃР»Рё РєР°РєР°СЏ-С‚Рѕ Р‘Р” С‚РѕР¶Рµ РІРµСЂРЅС‘С‚ "already exists"
            msg = (str(e) or "").lower()
            if "already exists" in msg:
                return None
            raise

    MetaData.create_all = _safe_create_all  # type: ignore[assignment]
    try:
        yield
    finally:
        MetaData.create_all = original_create_all  # type: ignore[assignment]


@pytest.fixture(scope="session", autouse=True)
def _bootstrap_minimal_models_for_mapping() -> None:
    """
    Р›С‘РіРєР°СЏ Р°РІС‚РѕР·Р°РіСЂСѓР·РєР° С‚РѕР»СЊРєРѕ РєСЂРёС‚РёС‡РЅС‹С… РєР»Р°СЃСЃРѕРІ, С‡С‚РѕР±С‹ СЃС‚СЂРѕРєРѕРІС‹Рµ relationship(...)
    РЅРµ РїР°РґР°Р»Рё РїСЂРё РєРѕРЅС„РёРіСѓСЂР°С†РёРё РјР°РїРїРµСЂРѕРІ РІ SQLite-СЋРЅРёС‚Р°С….
    """
    _bootstrap_minimal_models()


# ======================================================================================
# 3) РџР°С‚С‡ create_all РґР»СЏ Postgres (РїРѕР»РЅР°СЏ СЃС…РµРјР°) вЂ” РѕРґРёРЅ СЂР°Р· РЅР° СЃРµСЃСЃРёСЋ
# ======================================================================================

_MODELS_IMPORTED_ONCE = False
_CREATE_ALL_PATCHED = False


def _ensure_patch_create_all_for_postgres() -> None:
    """
    РџРђРўР§РРў Base.metadata.create_all РІРЅСѓС‚СЂРё app.models С‚Р°Рє, С‡С‚РѕР±С‹:
    - РґР»СЏ РЅРµ-SQLite РѕРґРёРЅ СЂР°Р· РІС‹Р·С‹РІР°С‚СЊ import_*_models(), Р·Р°С‚РµРј РѕР±С‹С‡РЅС‹Р№ create_all.
    - РґР»СЏ SQLite вЂ” РґРѕРІРµСЂСЏРµРј РіР»РѕР±Р°Р»СЊРЅРѕРјСѓ РјРѕРЅРёРїР°С‚С‡Сѓ MetaData.create_all (СЃРј. РІС‹С€Рµ).
    """
    global _CREATE_ALL_PATCHED
    import app.models as m  # type: ignore

    if _CREATE_ALL_PATCHED:
        return

    original = m.Base.metadata.create_all

    def _patched_create_all(*args, **kwargs):
        # <<< РєР»СЋС‡РµРІРѕР№ С„РёРєСЃ: РёСЃРїРѕР»СЊР·СѓРµРј РіР»РѕР±Р°Р»СЊРЅСѓСЋ РїРµСЂРµРјРµРЅРЅСѓСЋ РІРЅСѓС‚СЂРё РІР»РѕР¶РµРЅРЅРѕР№ С„СѓРЅРєС†РёРё
        global _MODELS_IMPORTED_ONCE

        bind = kwargs.get("bind")
        if bind is None and args:
            for a in args:
                if isinstance(a, Engine | Connection):
                    bind = a
                    break

        if not _is_sqlite_bind(bind):
            # РџРѕР»РЅР°СЏ Р·Р°РіСЂСѓР·РєР° РґРѕРјРµРЅР° СЃС‚СЂРѕРіРѕ РѕРґРёРЅ СЂР°Р·
            if not _MODELS_IMPORTED_ONCE and _import_all_models_once():
                _MODELS_IMPORTED_ONCE = True
        # РґР»СЏ SQLite вЂ” РЅРёС‡РµРіРѕ РѕСЃРѕР±РѕРіРѕ: РѕС‚СЂР°Р±РѕС‚Р°РµС‚ РіР»РѕР±Р°Р»СЊРЅС‹Р№ РјРѕРЅРєРёРїР°С‚С‡ MetaData.create_all
        kwargs.setdefault("checkfirst", True)
        try:
            return original(*args, **kwargs)
        except sa_exc.OperationalError as e:
            # РѕР±СЂР°Р±Р°С‚С‹РІР°РµРј РїРѕС‚РµРЅС†РёР°Р»СЊРЅС‹Рµ РґСѓР±Р»Рё В«already existsВ»
            msg = (str(e) or "").lower()
            if "already exists" in msg:
                return None
            raise

    m.Base.metadata.create_all = _patched_create_all  # type: ignore[assignment]
    _CREATE_ALL_PATCHED = True


# ======================================================================================
# 4) Р–РёР·РЅРµРЅРЅС‹Р№ С†РёРєР» СЃС…РµРјС‹ Postgres вЂ” РѕРґРёРЅ СЂР°Р· РЅР° СЃРµСЃСЃРёСЋ
# ======================================================================================


@pytest.fixture(scope="session")
def test_db() -> Iterator[None]:
    """Разворачиваем схему через Alembic upgrade head и откатываемся после тестов."""
    global _async_test_engine, sync_engine, TestingSessionLocal

    sync_url = SYNC_TEST_DATABASE_URL
    async_url = TEST_DATABASE_URL
    os.environ["DATABASE_URL"] = sync_url

    # Guard: operate only on test databases
    if "test" not in sync_url.lower():
        raise RuntimeError(f"Refusing to migrate non-test database URL: {sync_url}")
    if "test" not in async_url.lower():
        raise RuntimeError(f"Refusing to use non-test async database URL: {async_url}")

    # Dispose all existing engines before schema reset (if any were created)
    _dispose_test_engines()

    # Reset public schema to avoid duplicates across runs
    eng = sa.create_engine(sync_url, future=True)
    with eng.connect() as conn:
        conn = conn.execution_options(isolation_level="AUTOCOMMIT")
        try:
            conn.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
        except Exception:
            pass
        conn.execute(text("CREATE SCHEMA IF NOT EXISTS public"))
        # Best-effort default grants
        try:
            conn.execute(text("GRANT ALL ON SCHEMA public TO postgres"))
            conn.execute(text("GRANT ALL ON SCHEMA public TO public"))
        except Exception:
            pass
    eng.dispose()

    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", sync_url)

    # Try alembic upgrade; if it fails — FAIL FAST (otherwise schema will be incomplete)
    try:
        command.upgrade(cfg, "head")
    except Exception as e:
        raise RuntimeError("Alembic upgrade(head) failed; refusing to continue with partial schema") from e

    # Ensure alembic_version exists with a 256-char column for long revision ids
    with sa.create_engine(sync_url, future=True).begin() as conn:
        conn.execute(
            text(
                """
                DO $$
                BEGIN
                    IF NOT EXISTS (
                        SELECT 1 FROM information_schema.tables
                         WHERE table_schema='public'
                           AND table_name='alembic_version'
                    ) THEN
                        EXECUTE 'CREATE TABLE public.alembic_version (version_num VARCHAR(256) NOT NULL)';
                    ELSIF EXISTS (
                        SELECT 1 FROM information_schema.columns
                         WHERE table_schema='public'
                           AND table_name='alembic_version'
                           AND column_name='version_num'
                           AND (character_maximum_length IS NULL OR character_maximum_length < 256)
                    ) THEN
                        EXECUTE 'ALTER TABLE public.alembic_version ALTER COLUMN version_num TYPE VARCHAR(256)';
                    END IF;
                END$$;
                """
            )
        )

    # Explicitly create all ORM tables to ensure schema is complete
    # (alembic migration might not have all tables)
    from app.models.base import Base

    temp_engine = sa.create_engine(sync_url, future=True)
    try:
        # ORM models
        Base.metadata.create_all(temp_engine)

        # Wallet/payments are Core-only; ensure migrations created them
        from sqlalchemy import inspect

        insp = inspect(temp_engine)
        missing_tables = [
            name
            for name in ("wallet_accounts", "wallet_ledger", "wallet_payments")
            if not insp.has_table(name, schema="public")
        ]
        if missing_tables:
            raise RuntimeError(f"Test DB missing tables after migration: {', '.join(missing_tables)}")
    finally:
        temp_engine.dispose()

    # Re-create engines post-upgrade
    _async_test_engine = create_async_engine(
        async_url,
        echo=False,
        pool_pre_ping=True,
        poolclass=NullPool,
        future=True,
    )
    TestingSessionLocal = async_sessionmaker(
        bind=_async_test_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    sync_engine = sa.create_engine(
        sync_url,
        pool_pre_ping=True,
        poolclass=sa.pool.NullPool,
        future=True,
    )

    # Seed minimal companies for API tests (ids 1..4)
    try:
        from app.models.company import Company  # type: ignore

        SessionLocalSeed = sessionmaker(bind=sync_engine, expire_on_commit=False, autoflush=False)
        with SessionLocalSeed() as s:
            if (s.query(Company).count() or 0) < 4:
                s.add_all([Company(name=f"Company {i}") for i in range(1, 5)])
                s.commit()
    except Exception:
        pass

    try:
        yield
    finally:
        # Always release engines/sessions first; teardown must be idempotent
        _dispose_test_engines()

        keep_db = any(
            str(os.getenv(key, "")).strip().lower() in {"1", "true", "yes", "on"}
            for key in ("PYTEST_KEEP_DB", "KEEP_DB")
        )

        skip_downgrade = False
        reason = None
        if keep_db:
            skip_downgrade = True
            reason = "KEEP_DB flag is set"
        elif _TEST_RUN_INTERRUPTED:
            skip_downgrade = True
            reason = "test run was interrupted"

        if skip_downgrade:
            logger.warning("Skipping alembic downgrade: %s", reason)
        else:
            try:
                command.downgrade(cfg, "base")
            except Exception as e:  # noqa: PERF203 — best-effort cleanup
                logger.warning("Alebic downgrade failed: %s", e)


# ======================================================================================
# 5) FastAPI РєР»РёРµРЅС‚С‹ (async/sync) СЃ Р»РµРЅРёРІС‹РјРё РёРјРїРѕСЂС‚Р°РјРё app Рё get_db
# ======================================================================================


async def _override_get_db() -> AsyncIterator[AsyncSession]:
    global TestingSessionLocal
    if TestingSessionLocal is None:
        raise RuntimeError("TestingSessionLocal not initialized; test_db fixture must run first")
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        await session.close()


def _override_get_db_sync() -> Iterator[Any]:
    """Sync Session override for dependencies expecting sync get_db."""
    global sync_engine
    if sync_engine is None:
        raise RuntimeError("sync_engine not initialized; test_db fixture must run first")
    SessionLocal = sessionmaker(bind=sync_engine, expire_on_commit=False, autoflush=False)
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


@pytest_asyncio.fixture
async def async_client(test_db: None) -> AsyncIterator[AsyncClient]:
    app, get_async_db = _import_app_and_get_db()
    overrides: dict[Any, Any] = {get_async_db: _override_get_db}
    for sync_dep in _find_sync_get_db_funcs():
        overrides[sync_dep] = _override_get_db_sync
    app.dependency_overrides.update(overrides)
    # httpx 0.28+ removed the `app=` shortcut. Use ASGITransport, but keep backward-compat.
    try:
        async with AsyncClient(app=app, base_url="http://test") as client:
            try:
                yield client
            finally:
                app.dependency_overrides.clear()
            return
    except TypeError:
        transport = httpx.ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            try:
                yield client
            finally:
                app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def client(async_client: AsyncClient) -> AsyncIterator[AsyncClient]:
    yield async_client


# ======================================================================================
# 7) РЎРµСЃСЃРёРё Р‘Р” + С„Р°Р±СЂРёРєР° + Р±С‹СЃС‚СЂС‹Р№ СЃР±СЂРѕСЃ РґР°РЅРЅС‹С…
# ======================================================================================


@pytest_asyncio.fixture
async def async_db_session(test_db: None) -> AsyncIterator[AsyncSession]:
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        await session.close()


@pytest.fixture
def test_engine() -> AsyncEngine:
    """Compatibility alias for tests expecting `test_engine` fixture name."""
    global _async_test_engine
    if _async_test_engine is None:
        raise RuntimeError("test_engine not initialized; test_db fixture must run first")
    return _async_test_engine


# Backward-compat alias used by some tests
@pytest_asyncio.fixture
async def async_async_db_session(async_db_session: AsyncSession) -> AsyncIterator[AsyncSession]:
    yield async_db_session


@pytest.fixture
def db_session_factory() -> Callable[[], Awaitable[AsyncSession]]:
    async def _factory() -> AsyncSession:
        return TestingSessionLocal()

    return _factory


@pytest_asyncio.fixture(autouse=True)
async def db_reset(async_db_session: AsyncSession) -> AsyncIterator[None]:
    yield

    import app.models as m  # type: ignore

    # Collect existing tables in the target schema to avoid TRUNCATE on missing ones
    rows = await async_db_session.execute(
        text("select schemaname, tablename from pg_tables where schemaname = 'public'")
    )
    existing = {(r[0], r[1]) for r in rows}

    tablenames: list[str] = []
    for raw in m.Base.metadata.tables.keys():
        if raw == "alembic_version":
            continue
        parts = raw.split(".", 1)
        if len(parts) == 2:
            schema, table = parts
        else:
            schema, table = "public", parts[0]
        if (schema or "public", table) not in existing:
            continue
        tablenames.append(f'"{schema}"."{table}"' if schema else f'"{table}"')

    # Wallet/payments tables live outside Base.metadata; truncate them explicitly if present
    for tbl in ("wallet_accounts", "wallet_ledger", "wallet_payments"):
        if ("public", tbl) in existing:
            quoted = f'"public"."{tbl}"'
            if quoted not in tablenames:
                tablenames.append(quoted)

    if not tablenames:
        return

    sql = "TRUNCATE " + ", ".join(tablenames) + " RESTART IDENTITY CASCADE"
    await async_db_session.execute(text(sql))
    await async_db_session.commit()


# ======================================================================================
# 8) РЎСЌРјРїР»С‹ Рё С„Р°Р±СЂРёРєРё РґРѕРјРµРЅРЅС‹С… СЃСѓС‰РЅРѕСЃС‚РµР№
# ======================================================================================


@pytest.fixture
def sample_user_data() -> dict[str, object]:
    return {
        "phone": "77051234567",
        "email": "test@example.com",
        "full_name": "Test User",
        "password": "password123",
        "confirm_password": "password123",
        "username": "testuser",
    }


@pytest.fixture
def sample_product_data() -> dict[str, object]:
    return {
        "name": "Test Product",
        "slug": "test-product",
        "sku": "TEST-001",
        "description": "A test product",
        "price": 99.99,
        "stock_quantity": 100,
        "is_active": True,
    }


@pytest_asyncio.fixture
async def factory(async_db_session: AsyncSession) -> dict[str, Callable[..., Awaitable[object]]]:
    from app.models.company import Company  # type: ignore
    from app.models.product import Category, Product, ProductVariant  # type: ignore
    from app.models.user import User  # type: ignore
    from app.models.warehouse import ProductStock, Warehouse  # type: ignore

    async def create_company(name: str = "Acme Inc.") -> Company:
        obj = Company(name=name)
        async_db_session.add(obj)
        await async_db_session.commit()
        await async_db_session.refresh(obj)
        return obj

    async def create_user(
        *,
        username: str = "testuser",
        email: str = "test@example.com",
        phone: str = "+70000000000",
        company: Company | None = None,
        hashed_password: str = "",
    ) -> User:
        obj = User(username=username, email=email, phone=phone, hashed_password=hashed_password)
        if hasattr(obj, "company_id") and company is not None:
            setattr(obj, "company_id", company.id)
        async_db_session.add(obj)
        await async_db_session.commit()
        await async_db_session.refresh(obj)
        return obj

    async def create_category(*, name: str = "Default", slug: str = "default") -> Category:
        obj = Category(name=name, slug=slug)
        async_db_session.add(obj)
        await async_db_session.commit()
        await async_db_session.refresh(obj)
        return obj

    async def create_product(
        *,
        name: str = "Sample Product",
        slug: str = "sample-product",
        sku: str = "SKU-001",
        price: float = 100.0,
        stock_quantity: int = 10,
        category: Category | None = None,
        company: Company | None = None,
        is_active: bool = True,
    ) -> Product:
        if category is None:
            category = await create_category()
        kwargs: dict[str, object] = dict(
            name=name,
            slug=slug,
            sku=sku,
            price=price,
            stock_quantity=stock_quantity,
            category_id=category.id,
            is_active=is_active,
        )
        try:
            if "company_id" in Product.__table__.columns:  # type: ignore[attr-defined]
                if company is None:
                    company = await create_company()
                kwargs["company_id"] = company.id
        except Exception:
            pass

        obj = Product(**kwargs)  # type: ignore[arg-type]
        async_db_session.add(obj)
        await async_db_session.commit()
        await async_db_session.refresh(obj)
        return obj

    async def create_variant(
        *,
        product: Product | None = None,
        name: str = "Sample Variant",
        sku: str = "SKU-001-BLUE",
        price: float = 110.0,
        stock_quantity: int = 3,
        is_active: bool = True,
    ) -> ProductVariant:
        if product is None:
            product = await create_product()
        obj = ProductVariant(
            product_id=product.id,
            name=name,
            sku=sku,
            price=price,
            stock_quantity=stock_quantity,
            is_active=is_active,
        )
        async_db_session.add(obj)
        await async_db_session.commit()
        await async_db_session.refresh(obj)
        return obj

    async def create_warehouse(*, name: str = "Main WH", company: Company | None = None) -> Warehouse:
        obj = Warehouse(name=name)
        if hasattr(obj, "company_id"):
            if company is None:
                company = await create_company()
            setattr(obj, "company_id", company.id)
        async_db_session.add(obj)
        await async_db_session.commit()
        await async_db_session.refresh(obj)
        return obj

    async def create_stock(
        *,
        product: Product | None = None,
        warehouse: Warehouse | None = None,
        quantity: int = 7,
    ) -> ProductStock:
        if product is None:
            product = await create_product()
        if warehouse is None:
            warehouse = await create_warehouse()
        obj = ProductStock(product_id=product.id, warehouse_id=warehouse.id, quantity=quantity)
        async_db_session.add(obj)
        await async_db_session.commit()
        await async_db_session.refresh(obj)
        return obj

    return {
        "create_company": create_company,
        "create_user": create_user,
        "create_category": create_category,
        "create_product": create_product,
        "create_variant": create_variant,
        "create_warehouse": create_warehouse,
        "create_stock": create_stock,
    }


@pytest_asyncio.fixture(autouse=True)
async def _ensure_active_subscription(async_db_session: AsyncSession, request):
    path = str(getattr(request, "fspath", "") or "").lower()
    if "subscription" in path or "tenant_isolation_billing" in path:
        return
    if request.node.get_closest_marker("no_subscription"):
        return
    if "company_a_admin_headers" not in request.fixturenames and "company_b_admin_headers" not in request.fixturenames:
        return

    from datetime import UTC, datetime, timedelta
    from decimal import Decimal

    from sqlalchemy import select

    from app.models.billing import Subscription
    from app.models.company import Company

    now = datetime.now(UTC)
    for company_id in (1001, 2001):
        existing_company = (
            (await async_db_session.execute(select(Company).where(Company.id == company_id))).scalars().first()
        )
        if existing_company is None:
            async_db_session.add(Company(id=company_id, name=f"Company {company_id}"))
            await async_db_session.flush()

        existing = (
            (
                await async_db_session.execute(
                    select(Subscription)
                    .where(Subscription.company_id == company_id)
                    .where(Subscription.deleted_at.is_(None))
                )
            )
            .scalars()
            .first()
        )
        if existing:
            continue

        sub = Subscription(
            company_id=company_id,
            plan="start",
            status="active",
            billing_cycle="monthly",
            price=Decimal("0.00"),
            currency="KZT",
            started_at=now,
            period_start=now,
            period_end=now + timedelta(days=30),
            next_billing_date=now + timedelta(days=31),
        )
        async_db_session.add(sub)

    await async_db_session.commit()


# ======================================================================================
# 9) РЎРёРЅС…СЂРѕРЅРЅР°СЏ С„РёРєСЃС‚СѓСЂР° db_session (psycopg2) + auth_headers РґР»СЏ API
# ======================================================================================


@pytest.fixture
def db_session(test_db: None):
    """СЃРёРЅС…СЂРѕРЅРЅР°СЏ Session Рё С‚СЂР°РЅР·Р°РєС†РёРѕРЅРЅС‹Р№ rollback РґР»СЏ РєР°Р¶РґРѕРіРѕ С‚РµСЃС‚Р°."""
    global sync_engine
    if sync_engine is None:
        raise RuntimeError("sync_engine not initialized; test_db fixture must run first")
    connection = sync_engine.connect()
    trans = connection.begin()
    SessionLocal = sessionmaker(bind=connection, expire_on_commit=False, autoflush=False)
    session = SessionLocal()
    try:
        yield session
    finally:
        if trans.is_active:
            trans.rollback()
        session.close()
        connection.close()


@pytest.fixture
def auth_headers(test_db: None):
    """Seed a platform admin user and return its bearer token."""
    from app.core.security import create_access_token, get_password_hash  # type: ignore
    from app.models.billing import Subscription  # type: ignore
    from app.models.company import Company  # type: ignore
    from app.models.user import User  # type: ignore

    phone = "77000000001"
    password = "Secret123!"

    SessionLocal = sessionmaker(bind=sync_engine, expire_on_commit=False, autoflush=False)
    with SessionLocal() as s:
        existing_ids = {c.id for c in s.query(Company).all()}
        for cid in range(1, 5):
            if cid not in existing_ids:
                s.add(Company(id=cid, name=f"Company {cid}"))
        s.flush()

        company = s.get(Company, 1)
        if company is None:
            raise RuntimeError("Failed to seed company with id=1")

        existing_sub = (
            s.query(Subscription)
            .filter(Subscription.company_id == company.id)
            .filter(Subscription.deleted_at.is_(None))
            .first()
        )
        if existing_sub is None:
            sub = Subscription(
                company_id=company.id,
                plan="start",
                status="active",
                billing_cycle="monthly",
                price=0,
                currency="KZT",
            )
            s.add(sub)
            s.flush()

        user = s.query(User).filter(User.phone.in_([phone, f"+{phone}"])).first()
        if not user:
            user = User(
                company_id=company.id,
                phone=phone,
                hashed_password=get_password_hash(password),
                role="platform_admin",
                is_active=True,
                is_verified=True,
            )
            s.add(user)
        else:
            user.company_id = company.id
            user.role = "platform_admin"
            user.is_active = True
            user.is_verified = True
        s.commit()
        s.refresh(user)
        token = create_access_token(subject=user.id, extra={"company_id": user.company_id, "role": user.role})

    return {"Authorization": f"Bearer {token}"}


def _make_company_headers(
    *, company_id: int, role: str, phone: str, expires_delta: timedelta | None = None
) -> dict[str, str]:
    """Seed user with a specific role/company and return bearer headers."""
    from app.core.security import create_access_token, get_password_hash  # type: ignore
    from app.models.company import Company  # type: ignore
    from app.models.user import User  # type: ignore

    SessionLocal = sessionmaker(bind=sync_engine, expire_on_commit=False, autoflush=False)
    with SessionLocal() as s:
        company = s.get(Company, company_id)
        if company is None:
            company = Company(id=company_id, name=f"Company {company_id}")
            s.add(company)
            s.flush()

        user = s.query(User).filter(User.phone == phone).first()
        if not user:
            user = User(
                company_id=company.id,
                phone=phone,
                hashed_password=get_password_hash("Secret123!"),
                role=role,
                is_active=True,
                is_verified=True,
            )
            s.add(user)
        else:
            user.company_id = company.id
            user.role = role
            user.is_active = True
            user.is_verified = True
        s.commit()
        s.refresh(user)
        token = create_access_token(
            subject=user.id,
            extra={"company_id": user.company_id, "role": user.role},
            expires_delta=expires_delta,
        )

    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def company_a_admin_headers() -> dict[str, str]:
    return _make_company_headers(
        company_id=1001,
        role="admin",
        phone="+70000010001",
        expires_delta=timedelta(days=7),
    )


@pytest.fixture
def company_b_admin_headers() -> dict[str, str]:
    return _make_company_headers(
        company_id=2001,
        role="admin",
        phone="+70000020001",
        expires_delta=timedelta(days=7),
    )


@pytest.fixture
def company_a_manager_headers() -> dict[str, str]:
    return _make_company_headers(company_id=1001, role="manager", phone="+70000010002")


@pytest.fixture
def company_a_analyst_headers() -> dict[str, str]:
    return _make_company_headers(company_id=1001, role="analyst", phone="+70000010003")


@pytest.fixture
def company_a_storekeeper_headers() -> dict[str, str]:
    return _make_company_headers(company_id=1001, role="storekeeper", phone="+70000010004")
