# tests/conftest.py
"""
Pytest configuration and fixtures for async database testing.

РљР»СЋС‡РµРІС‹Рµ РѕСЃРѕР±РµРЅРЅРѕСЃС‚Рё:
- PostgreSQL (asyncpg) РєР°Рє РµРґРёРЅСЃС‚РІРµРЅРЅС‹Р№ РёСЃС‚РѕС‡РЅРёРє РёСЃС‚РёРЅС‹ РґР»СЏ РёРЅС‚РµРіСЂР°С†РёРѕРЅРЅС‹С… С‚РµСЃС‚РѕРІ.
- Р”Р»СЏ Postgres: РїРµСЂРµРґ create_all Р»РµРЅРёРІРѕ РїРѕРґРіСЂСѓР¶Р°РµРј РІРµСЃСЊ РґРѕРјРµРЅ (import_*_models) вЂ” РѕРґРёРЅ СЂР°Р·.
- Р”Р»СЏ SQLite: РіР»РѕР±Р°Р»СЊРЅС‹Р№ Р±РµР·РѕРїР°СЃРЅС‹Р№ create_all вЂ” СЃРѕР·РґР°С‘Рј С‚РѕР»СЊРєРѕ СЃР°РјРѕРґРѕСЃС‚Р°С‚РѕС‡РЅС‹Рµ С‚Р°Р±Р»РёС†С‹ (Р±РµР· В«РІРёСЃСЏС‡РёС…В» FK)
  Рё С‚РѕР»СЊРєРѕ СЃ С‚РёРїР°РјРё, РїРѕРґРґРµСЂР¶РёРІР°РµРјС‹РјРё SQLite (JSONB/ARRAY/INET/... РїСЂРѕРїСѓСЃРєР°РµРј).
- Р›С‘РіРєР°СЏ Р°РІС‚РѕР·Р°РіСЂСѓР·РєР° РєР»СЋС‡РµРІС‹С… РјРѕРґРµР»РµР№ (Company/User/Warehouse/AuditLog), С‡С‚РѕР±С‹ СЃС‚СЂРѕРєРѕРІС‹Рµ relationship(...)
  СЂРµР·РѕР»РІРёР»РёСЃСЊ Рё РЅРµ РїР°РґР°Р»Рё РјР°РїРїРµСЂС‹.
- РЈРґРѕР±РЅС‹Рµ С„РёРєСЃС‚СѓСЂС‹ РєР»РёРµРЅС‚Р° (sync/async), СЃРµСЃСЃРёР№, СЃР±СЂРѕСЃР° РґР°РЅРЅС‹С… Рё С„Р°Р±СЂРёРє РґРѕРјРµРЅРЅС‹С… СЃСѓС‰РЅРѕСЃС‚РµР№.
- РЇРІРЅРѕРµ DATABASE_URL (psycopg2) РґР»СЏ РїСЂРѕС…РѕР¶РґРµРЅРёСЏ test_database_url_default.
- Р”СЂСѓР¶РµСЃС‚РІРµРЅРЅР°СЏ РѕР±СЂР°Р±РѕС‚РєР° env: TEST_ASYNC_DATABASE_URL (РїСЂРµРґРїРѕС‡С‚РёС‚РµР»СЊРЅРѕ) РёР»Рё fallback Рє TEST_DATABASE_URL,
  РІРєР»СЋС‡Р°СЏ Р°РІС‚РѕРєРѕРЅРІРµСЂСЃРёСЋ РґСЂР°Р№РІРµСЂР° psycopg2 -> asyncpg РїСЂРё РЅРµРѕР±С…РѕРґРёРјРѕСЃС‚Рё (С‚РѕР»СЊРєРѕ РґР»СЏ С‚РµСЃС‚РѕРІРѕРіРѕ async engine).
- РџР°С‚С‡ СЃРёРЅС…СЂРѕРЅРЅРѕРіРѕ SQLAlchemy create_engine РґР»СЏ СЃРѕРІРјРµСЃС‚РёРјРѕСЃС‚Рё СЃ NullPool: РІС‹СЂРµР·Р°РµРј pool_* kwargs,
  С‡С‚РѕР±С‹ /api/auth/register РЅРµ РїР°РґР°Р» РІ С‚РµСЃС‚Р°С…, РєРѕС‚РѕСЂС‹Рµ СЃРѕР·РґР°СЋС‚ РєР»РёРµРЅС‚ Р±РµР· DI-РѕРІРµСЂСЂР°Р№РґРѕРІ.
- РџР°С‚С‡ TestClient: РµРіРѕ HTTP-РјРµС‚РѕРґС‹ РјРѕР¶РЅРѕ Р±РµР·РѕРїР°СЃРЅРѕ await-РёС‚СЊ РІ async-С‚РµСЃС‚Р°С….

Р’Рѕ РІСЃС‘Рј РјРѕРґСѓР»Рµ вЂ” UTF-8.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from typing import Any, Dict, List, Optional, Set, Tuple

import pytest
import pytest_asyncio

# SQLAlchemy
import sqlalchemy as sa
from fastapi.testclient import TestClient
from httpx import AsyncClient
from sqlalchemy import MetaData, Table
from sqlalchemy import exc as sa_exc  # РѕР±СЂР°Р±РѕС‚РєР° OperationalError/В«already existsВ»
from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool
from sqlalchemy.sql.type_api import TypeEngine  # С‚РёРїС‹ РґР»СЏ С„РёР»СЊС‚СЂР° unsupported

# ======================================================================================
# 0) Р‘СѓС‚СЃС‚СЂР°Рї РѕРєСЂСѓР¶РµРЅРёСЏ
# ======================================================================================

# Р’СЃСЋРґСѓ UTF-8 (С†РµРЅС‚СЂР°Р»РёР·РѕРІР°РЅРЅРѕ)
os.environ.setdefault("PYTHONIOENCODING", "UTF-8")

# Р§С‚РѕР±С‹ РїСЂРѕС€С‘Р» tests/app/core/test_config.py::test_database_url_default
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+psycopg2://postgres:admin123@localhost:5432/SmartSell",
)

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
        if poolclass is NullPool or (
            isinstance(poolclass, type) and issubclass(poolclass, NullPool)
        ):
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
      2) РёРЅР°С‡Рµ TEST_DATABASE_URL вЂ” РЅРѕ РїСЂРё РЅРµРѕР±С…РѕРґРёРјРѕСЃС‚Рё РєРѕРЅРІРµСЂС‚РёСЂСѓРµРј РґСЂР°Р№РІРµСЂ psycopg2 -> asyncpg
    Р‘СЂРѕСЃР°РµРј РїРѕРЅСЏС‚РЅРѕРµ РёСЃРєР»СЋС‡РµРЅРёРµ, РµСЃР»Рё URL РїСѓСЃС‚ РёР»Рё РЅРµ postgresql.
    """
    async_url = os.getenv("TEST_ASYNC_DATABASE_URL")
    base_url = os.getenv("TEST_DATABASE_URL")

    if async_url:
        url = async_url
    elif base_url:
        # Р•СЃР»Рё РєС‚Рѕ-С‚Рѕ РїРѕ РѕС€РёР±РєРµ РґР°Р» sync URL, Р°РєРєСѓСЂР°С‚РЅРѕ РєРѕРЅРІРµСЂС‚РёСЂСѓРµРј С‚РѕР»СЊРєРѕ РґСЂР°Р№РІРµСЂ.
        if base_url.startswith("postgresql+psycopg2://"):
            url = "postgresql+asyncpg://" + base_url.split("postgresql+psycopg2://", 1)[1]
        else:
            url = base_url
    else:
        # СЂР°Р·СѓРјРЅС‹Р№ РґРµС„РѕР»С‚ РїРѕРґ Р»РѕРєР°Р»СЊРЅСѓСЋ СЂР°Р·СЂР°Р±РѕС‚РєСѓ
        url = "postgresql+asyncpg://postgres:admin123@localhost:5432/SmartSellTest"

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
    get_db = None
    # РќРѕРІС‹Р№ РїСѓС‚СЊ
    try:
        from app.core.db import get_db as _get_db  # type: ignore

        get_db = _get_db
    except Exception:
        pass
    # РЎС‚Р°СЂС‹Р№ РїСѓС‚СЊ (РґР»СЏ РѕР±СЂР°С‚РЅРѕР№ СЃРѕРІРјРµСЃС‚РёРјРѕСЃС‚Рё)
    if get_db is None:
        try:
            from app.core.database import get_db as _get_db  # type: ignore

            get_db = _get_db
        except Exception as e:
            raise RuntimeError(
                f"Cannot import get_db from app.core.db or app.core.database: {e}"
            ) from e

    return app, get_db  # type: ignore[return-value]


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

TEST_DATABASE_URL = _get_async_test_url()

test_engine: AsyncEngine = create_async_engine(
    TEST_DATABASE_URL,
    echo=False,
    pool_pre_ping=True,
    poolclass=NullPool,  # РЅРµ РґРµСЂР¶РёРј РєРѕРЅРЅРµРєС‚С‹ вЂ” РїРѕР»РµР·РЅРѕ РґР»СЏ Windows/CI
    future=True,
)

TestingSessionLocal = async_sessionmaker(
    bind=test_engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


# ======================================================================================
# 2) Р“Р»РѕР±Р°Р»СЊРЅР°СЏ Р·Р°С‰РёС‚Р° create_all РґР»СЏ SQLite + РјРёРЅРёРјР°Р»СЊРЅР°СЏ Р°РІС‚РѕР·Р°РіСЂСѓР·РєР° РєР»Р°СЃСЃРѕРІ
# ======================================================================================


def _is_sqlite_bind(bind: Any) -> bool:
    """РћРїСЂРµРґРµР»СЏРµРј, С‡С‚Рѕ create_all РІС‹Р·С‹РІР°СЋС‚ РґР»СЏ SQLite (РѕСЃРѕР±РµРЅРЅРѕ :memory:)."""
    try:
        if isinstance(bind, (Engine, Connection)):
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
        if _sqlite_extract_target_table_names(t).issubset(present) and _sqlite_is_supported_table(
            t
        ):
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
                if isinstance(a, (Engine, Connection)):
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


@pytest_asyncio.fixture(scope="session")
async def test_db() -> AsyncIterator[None]:
    """
    РЎРѕР·РґР°С‚СЊ Р’РЎР® СЃС…РµРјСѓ Р‘Р” РѕРґРёРЅ СЂР°Р· РїРµСЂРµРґ С‚РµСЃС‚Р°РјРё Рё СЃРЅРµСЃС‚Рё РµС‘ РїРѕСЃР»Рµ.
    РўРѕР»СЊРєРѕ РґР»СЏ Postgres (РёРЅС‚РµРіСЂР°С†РёРѕРЅРЅС‹Рµ С‚РµСЃС‚С‹).
    """
    global _MODELS_IMPORTED_ONCE  # С„РёРєСЃ UnboundLocalError РІ СЌС‚РѕР№ РєРѕСЂСѓС‚РёРЅРµ С‚РѕР¶Рµ

    _ensure_patch_create_all_for_postgres()

    import app.models as m  # type: ignore

    if not _MODELS_IMPORTED_ONCE:
        # Р•СЃР»Рё import_* РЅРµРґРѕСЃС‚СѓРїРµРЅ вЂ” fallback РЅР° РјРёРЅРёРјР°Р»СЊРЅС‹Р№ bootstrap
        if not _import_all_models_once():
            _bootstrap_minimal_models()
        _MODELS_IMPORTED_ONCE = True  # РѕС‚РјРµС‚РёС‚СЊ, С‡С‚Рѕ РґРѕРјРµРЅ Р·Р°РіСЂСѓР¶РµРЅ

    async with test_engine.begin() as conn:
        await conn.run_sync(lambda sync_conn: m.Base.metadata.create_all(bind=sync_conn))

    try:
        if hasattr(m, "assert_relationships_resolved"):
            m.assert_relationships_resolved()  # type: ignore[attr-defined]
    except Exception as e:
        raise RuntimeError(f"Model relationship/FK unresolved after create_all: {e}") from e

    try:
        yield
    finally:
        async with test_engine.begin() as conn:
            await conn.run_sync(lambda sync_conn: m.Base.metadata.drop_all(bind=sync_conn))


# ======================================================================================
# 5) Event loop РґР»СЏ pytest-asyncio (strict mode СЃРѕРІРјРµСЃС‚РёРј)
# ======================================================================================


@pytest.fixture(scope="session")
def event_loop() -> Iterator[asyncio.AbstractEventLoop]:
    loop = asyncio.new_event_loop()
    try:
        yield loop
    finally:
        loop.close()


# ======================================================================================
# 6) FastAPI РєР»РёРµРЅС‚С‹ (async/sync) СЃ Р»РµРЅРёРІС‹РјРё РёРјРїРѕСЂС‚Р°РјРё app Рё get_db
# ======================================================================================


async def _override_get_db() -> AsyncIterator[AsyncSession]:
    async with TestingSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()


@pytest_asyncio.fixture
async def async_client(test_db: None) -> AsyncIterator[AsyncClient]:
    app, get_db = _import_app_and_get_db()
    app.dependency_overrides[get_db] = _override_get_db  # type: ignore[index]
    async with AsyncClient(app=app, base_url="http://test") as client:
        try:
            yield client
        finally:
            app.dependency_overrides.clear()


@pytest.fixture
def client(test_db: None) -> Iterator[TestClient]:
    app, get_db = _import_app_and_get_db()
    app.dependency_overrides[get_db] = _override_get_db  # type: ignore[index]
    with TestClient(app) as c:
        try:
            yield c
        finally:
            app.dependency_overrides.clear()


# ======================================================================================
# 7) РЎРµСЃСЃРёРё Р‘Р” + С„Р°Р±СЂРёРєР° + Р±С‹СЃС‚СЂС‹Р№ СЃР±СЂРѕСЃ РґР°РЅРЅС‹С…
# ======================================================================================


@pytest_asyncio.fixture
async def async_db_session(test_db: None) -> AsyncIterator[AsyncSession]:
    async with TestingSessionLocal() as session:
        yield session


@pytest.fixture
def db_session_factory() -> Callable[[], Awaitable[AsyncSession]]:
    async def _factory() -> AsyncSession:
        return TestingSessionLocal()

    return _factory


@pytest_asyncio.fixture
async def db_reset(async_db_session: AsyncSession) -> AsyncIterator[None]:
    yield

    import app.models as m  # type: ignore

    tablenames = [t for t in m.Base.metadata.tables.keys() if t != "alembic_version"]
    if not tablenames:
        return
    sql = "TRUNCATE " + ", ".join(f'"{name}"' for name in tablenames) + " RESTART IDENTITY CASCADE"
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

    async def create_warehouse(
        *, name: str = "Main WH", company: Company | None = None
    ) -> Warehouse:
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


# ======================================================================================
# 9) РЎРёРЅС…СЂРѕРЅРЅР°СЏ С„РёРєСЃС‚СѓСЂР° db_session (psycopg2) + auth_headers РґР»СЏ API
# ======================================================================================
import os

import sqlalchemy as sa
from sqlalchemy.orm import sessionmaker

# Р‘РµСЂС‘Рј sync-DSN: СЏРІРЅС‹Р№ TEST_DATABASE_URL_SYNC, РёРЅР°С‡Рµ DATABASE_URL.
# РћР‘РЇР—РђРўР•Р›Р¬РќРћ СЃ sslmode=disable (С‡С‚РѕР±С‹ РЅРµ С‚СЂРµР±РѕРІР°С‚СЊ SSL Сѓ Р»РѕРєР°Р»СЊРЅРѕРіРѕ Postgres).
SYNC_DATABASE_URL = (
    os.getenv("TEST_DATABASE_URL_SYNC")
    or os.getenv("DATABASE_URL")
    or "postgresql+psycopg2://postgres:admin123@localhost:5432/SmartSellTest?sslmode=disable"
)

sync_engine = sa.create_engine(
    SYNC_DATABASE_URL,
    pool_pre_ping=True,
    poolclass=sa.pool.NullPool,
    future=True,
)

SessionLocal = sessionmaker(bind=sync_engine, expire_on_commit=False, autoflush=False)


@pytest.fixture
def db_session():
    """
    РЎРРќРҐР РћРќРќРђРЇ СЃРµСЃСЃРёСЏ РґР»СЏ С‚РµСЃС‚РѕРІ, РєРѕС‚РѕСЂС‹Рµ Р¶РґСѓС‚ РёРјРµРЅРЅРѕ sync Session.
    Р–РёРІС‘С‚ РІ РїСЂРµРґРµР»Р°С… С‚РµСЃС‚Р°, Р°РєРєСѓСЂР°С‚РЅРѕ Р·Р°РєСЂС‹РІР°РµС‚СЃСЏ.
    """
    with SessionLocal() as s:
        yield s


@pytest.fixture
def auth_headers(client):
    """
    Р РµРіРёСЃС‚СЂРёСЂСѓРµС‚ РїРѕР»СЊР·РѕРІР°С‚РµР»СЏ (РµСЃР»Рё СѓР¶Рµ РµСЃС‚СЊ вЂ” РЅРµ СЃС‚СЂР°С€РЅРѕ) Рё Р»РѕРіРёРЅРёС‚ РµРіРѕ,
    РІРѕР·РІСЂР°С‰Р°СЏ Р·Р°РіРѕР»РѕРІРѕРє Authorization РґР»СЏ API-С‚РµСЃС‚РѕРІ РїРѕРґРїРёСЃРѕРє.
    """
    phone = "+77000000001"
    password = "Secret123!"

    # РџС‹С‚Р°РµРјСЃСЏ Р·Р°СЂРµРіРёСЃС‚СЂРёСЂРѕРІР°С‚СЊ; РµСЃР»Рё СѓР¶Рµ РµСЃС‚СЊ вЂ” РѕРє.
    client.post(
        "/api/auth/register", json={"phone": phone, "password": password, "full_name": "Test User"}
    )

    r = client.post("/api/auth/login", json={"phone": phone, "password": password})
    assert r.status_code == 200, r.text
    data = r.json()
    token = (
        data.get("access_token")
        or (data.get("data") or {}).get("access_token")
        or (data.get("result") or {}).get("access_token")
    )
    assert token, f"РќРµ СѓРґР°Р»РѕСЃСЊ РёР·РІР»РµС‡СЊ access_token РёР· РѕС‚РІРµС‚Р°: {data}"
    return {"Authorization": f"Bearer {token}"}
