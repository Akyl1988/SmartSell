"""
Unified database configuration and session management for SmartSell (async + sync).

Ключевые особенности:
- 💡 Ленивое создание движков (никаких подключений при импорте модуля).
- ✅ Безопасный фолбэк: async — sqlite+aiosqlite:///:memory: (если URL не задан/невалиден).
- 🔄 Автоконвертация Postgres URL:
    - async  → postgresql+asyncpg://
    - sync   → postgresql+psycopg2://
- 📖/✍️ RW-routing через контекстный менеджер RWRoute (реплика для чтений).
- 🧪 Дружелюбно к pytest (NullPool, без eager-connect).
- 🧰 Утилиты:
    Async: get_async_db(), get_async_session(), init_db_async(), close_db_async(),
           reload_async_engine(), health_check_db_async(), ensure_extensions_async()
    Sync:  get_db(), get_session(), session_scope(), init_db(), drop_db(), recreate_db(),
           dispose_engine(), reload_engine(), health_check_db(), ensure_extensions()
    Alembic: get_alembic_engine_url()
- 📊 Простые метрики времени SQL (in-memory) + best-effort OTEL.

Совместимость:
- Старые импорты `from app.core.db import get_async_session` и `get_session` РАБОТАЮТ.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time as _time
from collections.abc import AsyncIterator, Generator, Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.engine.url import make_url
from sqlalchemy.exc import IllegalStateChangeError, OperationalError, SQLAlchemyError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

logger = logging.getLogger(__name__)

# -----------------------------------------------------------------------------
# Settings (безопасный импорт)
# -----------------------------------------------------------------------------
try:
    from app.core.config import (
        _under_pytest,
        db_url_fingerprint,
        get_settings,
        resolve_async_database_url,
        resolve_database_url,
    )

    settings = get_settings()
except Exception:

    class _S:
        ENVIRONMENT = os.getenv("ENVIRONMENT", "development")
        DEBUG = os.getenv("DEBUG", "0") in ("1", "true", "True")
        PROJECT_NAME = "SmartSell"
        VERSION = "0.1.0"
        DATABASE_URL = os.getenv("DATABASE_URL", "")
        PG_SEARCH_PATH = os.getenv("PG_SEARCH_PATH", "")
        POSTGRES_STATEMENT_TIMEOUT_MS = int(os.getenv("POSTGRES_STMT_TIMEOUT_MS", "0") or 0)

        def sqlalchemy_engine_options_effective(self, async_engine: bool) -> dict:
            return {}

    settings = _S()  # type: ignore

    def _under_pytest() -> bool:  # type: ignore
        return False


# -----------------------------------------------------------------------------
# Base импортируется из models (единая точка истины для DeclarativeBase)
# -----------------------------------------------------------------------------
try:
    from app.models.base import Base
except ImportError:
    # Fallback на случай ранних импортов (до инициализации models пакета)
    from sqlalchemy.orm import DeclarativeBase

    class Base(DeclarativeBase):
        pass


__all__ = [
    # Base
    "Base",
    # Async
    "async_session_maker",
    "get_async_db",
    "get_async_session",  # совместимость
    "init_db_async",
    "close_db_async",
    "reload_async_engine",
    "health_check_db_async",
    "ensure_extensions_async",
    # Sync
    "get_db",
    "get_session",  # совместимость
    "session_scope",
    "init_db",
    "drop_db",
    "recreate_db",
    "dispose_engine",
    "reload_engine",
    "health_check_db",
    "ensure_extensions",
    # Alembic
    "get_alembic_engine_url",
    # RW routing + stats
    "RWRoute",
    "get_query_stats",
]


# -----------------------------------------------------------------------------
# Вспомогательные: нормализация URL
# -----------------------------------------------------------------------------
def _normalize_pg_to_asyncpg(url: str) -> str:
    """Convert any Postgres URL to asyncpg and strip unsupported sslmode.

    Handles forms:
    - postgres://...
    - postgresql://...
    - postgresql+<driver>://...

    Removes any `sslmode` query parameter (disable/require/prefer, etc.) which
    asyncpg does not accept. Other query params are preserved.
    """
    try:
        # Normalize base scheme
        if url.startswith("postgres://"):
            url = url.replace("postgres://", "postgresql://", 1)

        u = make_url(url)

        # Force asyncpg driver
        base_driver = u.drivername.split("+", 1)[0]
        u = u.set(drivername=f"{base_driver}+asyncpg")

        # Strip any sslmode from query params (asyncpg does not accept it)
        if u.query:
            q = dict(u.query)
            if "sslmode" in q:
                q.pop("sslmode", None)
                u = u.set(query=q)

        return u.render_as_string(hide_password=False)
    except Exception:
        # Fallback to original heuristic if parsing failed
        if url.startswith("postgresql+"):
            url = "postgresql+asyncpg://" + url.split("://", 1)[1]
            # best-effort strip
            url = url.replace("sslmode=disable", "").replace("sslmode=require", "")
            return url
        if url.startswith("postgresql://"):
            url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
            url = url.replace("sslmode=disable", "").replace("sslmode=require", "")
            return url
        return url


def _normalize_pg_to_psycopg2(url: str) -> str:
    try:
        if url.startswith("postgres://"):
            url = url.replace("postgres://", "postgresql://", 1)
        u = make_url(url)
        base_driver = u.drivername.split("+", 1)[0]
        u = u.set(drivername=f"{base_driver}+psycopg2")
        return u.render_as_string(hide_password=False)
    except Exception:
        if url.startswith("postgresql+psycopg2://"):
            return url
        if url.startswith("postgresql+asyncpg://"):
            return url.replace("postgresql+asyncpg://", "postgresql+psycopg2://", 1)
        if url.startswith("postgresql://"):
            return url.replace("postgresql://", "postgresql+psycopg2://", 1)
        return url


def _assert_non_local_has_real_db(source: str) -> None:
    if _under_pytest():
        return
    env_val = str(getattr(settings, "ENVIRONMENT", "") or "").lower()
    if source == "DEFAULT" and env_val != "local":
        raise RuntimeError("DATABASE_URL is required for non-local environments")


def _validate_is_postgres(url: str) -> None:
    if not (url.startswith("postgresql://") or url.startswith("postgresql+") or url.startswith("postgres://")):
        raise RuntimeError(
            f"PostgreSQL required, got DATABASE_URL='{url}'. " "Use 'postgresql+psycopg2://user:pass@host:port/dbname'."
        )


# -----------------------------------------------------------------------------
# Определение URL с дефолтами/фолбэками
# -----------------------------------------------------------------------------
def _resolve_async_url() -> str:
    base_url, source, _ = resolve_database_url(settings)
    _assert_non_local_has_real_db(source)

    candidates: list[str] = []
    try:
        override = getattr(settings, "sqlalchemy_async_url", "").strip()
        if override:
            candidates.append(override)
    except Exception:
        pass

    try:
        urls = getattr(settings, "sqlalchemy_urls", {}) or {}
        async_url = (urls.get("async") or "").strip()
        if async_url:
            candidates.append(async_url)
    except Exception:
        pass

    candidates.append(base_url.strip())

    for raw in candidates:
        if not raw:
            continue
        try:
            url = _normalize_pg_to_asyncpg(raw)
            make_url(url)
            return url
        except Exception:
            logger.warning("Invalid async DB URL candidate skipped", exc_info=False)

    return "sqlite+aiosqlite:///:memory:"


def _resolve_sync_pg_url() -> str:
    base_url, source, _ = resolve_database_url(settings)
    _assert_non_local_has_real_db(source)

    candidates: list[str] = []

    def _append_candidate(raw: str | None) -> None:
        if not raw:
            return
        try:
            parsed = make_url(raw)
            if parsed.password is not None and set(parsed.password) <= {"*"}:
                return
        except Exception:
            pass
        candidates.append(raw)

    try:
        override = getattr(settings, "sqlalchemy_sync_url", "").strip()
        _append_candidate(override)
    except Exception:
        pass

    try:
        urls = getattr(settings, "sqlalchemy_urls", {}) or {}
        sync_url = (urls.get("sync") or "").strip()
        _append_candidate(sync_url)
    except Exception:
        pass

    if base_url:
        candidates.append(base_url.strip())

    for raw in candidates:
        if not raw:
            continue
        _validate_is_postgres(raw)
        url = _normalize_pg_to_psycopg2(raw)
        make_url(url)
        return url

    raise RuntimeError("DATABASE_URL is not set. PostgreSQL is required for sync engine.")


# -----------------------------------------------------------------------------
# Engine options (безопасно читаем из settings)
# -----------------------------------------------------------------------------
def _engine_options(async_engine: bool) -> dict:
    try:
        opts = settings.sqlalchemy_engine_options_effective(async_engine=async_engine)  # type: ignore[attr-defined]
        if not isinstance(opts, dict):
            raise TypeError
    except Exception:
        opts = {}
    # дефолты
    if async_engine:
        opts.setdefault("future", True)
    opts.setdefault("pool_pre_ping", True)
    opts.setdefault("pool_recycle", 1800)
    # pytest — аккуратнее
    if "PYTEST_CURRENT_TEST" in os.environ or getattr(settings, "ENVIRONMENT", "") == "test":
        opts.setdefault("poolclass", NullPool)
    poolclass = opts.get("poolclass")
    if poolclass is NullPool or (isinstance(poolclass, type) and issubclass(poolclass, NullPool)):
        for bad in ("pool_size", "max_overflow", "pool_timeout", "pool_recycle", "pool_use_lifo"):
            opts.pop(bad, None)
    # echo по DEBUG
    if "echo" not in opts:
        opts["echo"] = bool(getattr(settings, "DEBUG", False))
    return opts


# -----------------------------------------------------------------------------
# RW routing (read/write) через contextvar
# -----------------------------------------------------------------------------
_rw_mode: ContextVar[str] = ContextVar("_rw_mode", default="write")  # "read" | "write"


class RWRoute:
    """with RWRoute('read'): ... → чтения уходят в реплику (если настроена)."""

    def __init__(self, mode: str = "read"):
        if mode not in ("read", "write"):
            raise ValueError("mode must be 'read' or 'write'")
        self.mode = mode
        self._tok = None

    def __enter__(self):
        self._tok = _rw_mode.set(self.mode)

    def __exit__(self, exc_type, exc, tb):
        if self._tok:
            _rw_mode.reset(self._tok)


# -----------------------------------------------------------------------------
# Ленивая инициализация ASYNC и SYNC движков/фабрик сессий
# -----------------------------------------------------------------------------
_ASYNC_ENGINE: AsyncEngine | None = None
_ASYNC_SESSION_MAKER: async_sessionmaker[AsyncSession] | None = None
_ASYNC_REPLICA_ENGINE: AsyncEngine | None = None

_SYNC_ENGINE: Engine | None = None
_SYNC_SESSION_MAKER: sessionmaker | None = None
_SYNC_REPLICA_ENGINE: Engine | None = None

# простая in-memory статистика
_query_stats: dict[str, dict[str, float]] = {}


def _password_present(url: str) -> bool:
    try:
        parsed = make_url(url)
        return bool(parsed.password)
    except Exception:
        return False


def _log_effective_url(url: str, *, mode: str, source: str | None = None) -> None:
    """Логируем безопасную сводку по DSN перед созданием engine."""
    try:
        parsed = make_url(url)
        fp = db_url_fingerprint(url)
        logger.info(
            "db_url_resolved mode=%s driver=%s user=%s host=%s port=%s db=%s source=%s fp=%s password_present=%s",
            mode,
            parsed.drivername,
            parsed.username or "",
            parsed.host or "",
            parsed.port or "",
            parsed.database or "",
            (source or getattr(settings, "db_url_source", lambda: "unknown")()),
            fp,
            bool(parsed.password),
        )
    except Exception as e:
        logger.warning("db_url_resolved mode=%s failed to log: %s", mode, e)


def _install_query_metrics_on_sync_engine(eng: Engine) -> None:
    @event.listens_for(eng, "before_cursor_execute")
    def before_cursor_execute(conn, cursor, statement, params, context, executemany):
        context._q_start = _time.perf_counter()

    @event.listens_for(eng, "after_cursor_execute")
    def after_cursor_execute(conn, cursor, statement, params, context, executemany):
        start = getattr(context, "_q_start", None)
        if start is None:
            return
        dur = (_time.perf_counter() - start) * 1000.0
        key = ((statement or "").strip().split(None, 1)[0] or "SQL").upper()
        bucket = _query_stats.setdefault(key, {"count": 0.0, "total_ms": 0.0})
        bucket["count"] += 1.0
        bucket["total_ms"] += dur


def get_query_stats() -> dict[str, dict[str, float]]:
    return {k: dict(v) for k, v in _query_stats.items()}


def async_session_maker(**kwargs):
    """Backwards-compatible async session factory helper."""
    _get_async_engine()
    maker = _ASYNC_SESSION_MAKER
    if maker is None:
        raise RuntimeError("Async session maker is not initialized")
    return maker(**kwargs)


def get_async_session_maker() -> async_sessionmaker[AsyncSession]:
    """Return the shared async sessionmaker (initialized lazily)."""
    _get_async_engine()
    if _ASYNC_SESSION_MAKER is None:
        raise RuntimeError("Async session maker is not initialized")
    return _ASYNC_SESSION_MAKER


def _get_async_engine() -> AsyncEngine:
    """Создаёт и кэширует async engine лениво (без подключения)."""
    global _ASYNC_ENGINE, _ASYNC_REPLICA_ENGINE, _ASYNC_SESSION_MAKER
    if _ASYNC_ENGINE is not None:
        return _ASYNC_ENGINE

    url, source, fp = resolve_async_database_url(settings)

    if logger.isEnabledFor(logging.DEBUG) or os.getenv("PYTEST_DEBUG_ERRORS"):
        try:
            parsed = make_url(url)
            logger.debug(
                "db_url_async_resolved",
                extra={
                    "source": source,
                    "fp_no_pw": fp,
                    "user": parsed.username or "",
                    "host": parsed.host or "",
                    "port": parsed.port or "",
                    "db": parsed.database or "",
                    "has_password": bool(parsed.password),
                },
            )
        except Exception:
            pass

    _log_effective_url(url, mode="async", source=source)
    _ASYNC_ENGINE = create_async_engine(url, **_engine_options(async_engine=True))
    _ASYNC_SESSION_MAKER = async_sessionmaker(
        bind=_ASYNC_ENGINE,
        expire_on_commit=False,
        class_=AsyncSession,
        autoflush=False,
        autocommit=False,
    )

    # replica (опционально)
    rep = (os.getenv("DATABASE_REPLICA_URL", "") or "").strip()
    if rep:
        try:
            rep_url = _normalize_pg_to_asyncpg(rep)
            make_url(rep_url)
            _ASYNC_REPLICA_ENGINE = create_async_engine(rep_url, **_engine_options(async_engine=True))
        except Exception as e:
            logger.warning("Async replica init failed; primary will be used. %s", e)

    # метрики повесим на underlying sync_engine
    try:
        _install_query_metrics_on_sync_engine(_ASYNC_ENGINE.sync_engine)
        if _ASYNC_REPLICA_ENGINE:
            _install_query_metrics_on_sync_engine(_ASYNC_REPLICA_ENGINE.sync_engine)
    except Exception:
        pass

    return _ASYNC_ENGINE


def _get_sync_engine() -> Engine:
    """Создаёт и кэширует sync engine лениво (без подключения)."""
    global _SYNC_ENGINE, _SYNC_SESSION_MAKER, _SYNC_REPLICA_ENGINE
    if _SYNC_ENGINE is not None:
        return _SYNC_ENGINE

    def _dsn_diagnostics_enabled() -> bool:
        return os.getenv("DB_DSN_DIAGNOSTICS", "").lower() in {"1", "true", "yes", "on"}

    def _log_dsn_diagnostic(message: str) -> None:
        if _dsn_diagnostics_enabled():
            logger.warning(message)

    def _is_masked_password(parsed_url) -> bool:
        try:
            if not parsed_url.username:
                return False
            if parsed_url.password is None:
                return True
            return set(parsed_url.password) <= {"*"}
        except Exception:
            return False

    def _fallback_unmasked_url(raw_url: str) -> str:
        try:
            parsed = make_url(raw_url)
            if not _is_masked_password(parsed):
                return raw_url
        except Exception:
            return raw_url

        fallback_url = _resolve_sync_pg_url()
        try:
            parsed_fallback = make_url(fallback_url)
            if _is_masked_password(parsed_fallback):
                _log_dsn_diagnostic("Masked sync DSN detected; fallback still masked, keeping original")
                return raw_url
        except Exception:
            return raw_url

        _log_dsn_diagnostic("Masked sync DSN detected; falling back to resolved DATABASE_URL")
        return fallback_url

    def _install_pg_connection_events(engine: Engine) -> None:
        app_name = f"{getattr(settings, 'PROJECT_NAME', 'SmartSell')}@{getattr(settings, 'VERSION', '')}".strip("@")
        pg_search_path = getattr(settings, "PG_SEARCH_PATH", "") or os.getenv("PG_SEARCH_PATH", "")
        try:
            stmt_timeout_ms = int(getattr(settings, "POSTGRES_STATEMENT_TIMEOUT_MS", 0) or 0)
        except Exception:
            stmt_timeout_ms = 0

        @event.listens_for(engine, "connect")
        def _on_connect(dbapi_conn, _conn_rec):  # pragma: no cover (низкоуровневая настройка)
            try:
                cur = dbapi_conn.cursor()
                cur.execute("SET TIME ZONE 'UTC'")
                cur.execute("SET standard_conforming_strings = on")
                try:
                    cur.execute("SET application_name = %s", (app_name,))
                except Exception:
                    cur.execute(f"SET application_name = '{app_name}'")
                if pg_search_path:
                    cur.execute(f"SET search_path = {pg_search_path}")
                if stmt_timeout_ms > 0:
                    cur.execute(f"SET statement_timeout = {stmt_timeout_ms}")
                cur.close()
            except Exception as e:
                logger.warning("PG on_connect setup failed: %s", e)

    url = _resolve_sync_pg_url()
    try:
        if _is_masked_password(make_url(url)):
            url = _fallback_unmasked_url(url)
    except Exception:
        pass
    # Recompute source for sync engine logging
    _, source, _ = resolve_database_url(settings)
    _log_effective_url(url, mode="sync", source=source)
    _SYNC_ENGINE = create_engine(url, **_engine_options(async_engine=False))
    _install_pg_connection_events(_SYNC_ENGINE)
    _SYNC_SESSION_MAKER = sessionmaker(bind=_SYNC_ENGINE, autocommit=False, autoflush=False, expire_on_commit=False)

    # replica (опционально)
    rep = (os.getenv("DATABASE_REPLICA_URL_SYNC", "") or "").strip()
    if rep:
        try:
            rep_url = _normalize_pg_to_psycopg2(rep)
            make_url(rep_url)
            if _is_masked_password(make_url(rep_url)):
                _log_dsn_diagnostic("Masked replica DSN detected; skipping replica engine")
            else:
                _SYNC_REPLICA_ENGINE = create_engine(rep_url, **_engine_options(async_engine=False))
                _install_pg_connection_events(_SYNC_REPLICA_ENGINE)
        except Exception as e:
            logger.warning("Sync replica init failed; primary will be used. %s", e)

    try:
        _install_query_metrics_on_sync_engine(_SYNC_ENGINE)
        if _SYNC_REPLICA_ENGINE:
            _install_query_metrics_on_sync_engine(_SYNC_REPLICA_ENGINE)
    except Exception:
        pass

    return _SYNC_ENGINE


# -----------------------------------------------------------------------------
# Async API (FastAPI dependency и утилиты)
# -----------------------------------------------------------------------------
class _RoutingAsyncSession(AsyncSession):
    async def get_bind(self, mapper=None, clause=None, **kw):  # type: ignore[override]
        mode = _rw_mode.get()
        if mode == "read" and _ASYNC_REPLICA_ENGINE is not None:
            return _ASYNC_REPLICA_ENGINE.sync_engine
        return _get_async_engine().sync_engine


async def get_async_db(use_routing: bool = False) -> AsyncIterator[AsyncSession]:
    """
    FastAPI dependency (async).
    Создаёт сессию при входе и закрывает при выходе. Подключение к БД происходит здесь,
    а не при импорте модуля.
    """
    engine = _get_async_engine()
    maker = async_sessionmaker(
        bind=engine,
        class_=_RoutingAsyncSession if use_routing else AsyncSession,
        expire_on_commit=False,
        autoflush=False,
        autocommit=False,
    )
    session = maker()
    try:
        yield session
    finally:
        try:
            await asyncio.shield(session.close())
        except IllegalStateChangeError as exc:
            logger.debug("Async session close skipped: %s", exc)


# ✅ Совместимый алиас для старых импортов
async def get_async_session() -> AsyncIterator[AsyncSession]:
    async for s in get_async_db():
        yield s


async def init_db_async(drop_all: bool = False) -> None:
    raise RuntimeError("Database schema must be managed by Alembic, not create_all")


async def close_db_async() -> None:
    if _ASYNC_REPLICA_ENGINE is not None:
        try:
            await _ASYNC_REPLICA_ENGINE.dispose()
        except Exception:
            pass
    if _ASYNC_ENGINE is not None:
        try:
            await _ASYNC_ENGINE.dispose()
        except Exception:
            pass


async def reload_async_engine() -> None:
    global _ASYNC_ENGINE, _ASYNC_SESSION_MAKER, _ASYNC_REPLICA_ENGINE
    await close_db_async()
    _ASYNC_ENGINE = None
    _ASYNC_REPLICA_ENGINE = None
    _ASYNC_SESSION_MAKER = None
    _get_async_engine()  # recreate (лениво без коннекта)


async def health_check_db_async(timeout_seconds: int = 2) -> dict:
    from sqlalchemy import text as sqltext

    try:
        eng = _get_async_engine()
        async with eng.connect() as conn:
            await conn.execution_options(timeout=timeout_seconds).execute(sqltext("SELECT 1"))
            version = None
            try:
                if str(eng.url).startswith("sqlite"):
                    version = (await conn.execute(sqltext("SELECT sqlite_version()"))).scalar_one_or_none()
                else:
                    version = (await conn.execute(sqltext("SHOW server_version"))).scalar_one_or_none()
            except Exception:
                version = None
        return {"ok": True, "error": None, "server_version": version}
    except Exception as e:
        logger.error("Async DB health check failed: %s", e)
        return {"ok": False, "error": str(e), "server_version": None}


async def ensure_extensions_async() -> None:
    """Создаёт полезные расширения в Postgres (если возможно). На SQLite — noop."""
    eng = _get_async_engine()
    if not str(eng.url).startswith("postgresql+asyncpg://"):
        return
    stmts = [
        'CREATE EXTENSION IF NOT EXISTS "uuid-ossp"',
        "CREATE EXTENSION IF NOT EXISTS pg_trgm",
        "CREATE EXTENSION IF NOT EXISTS citext",
    ]
    from sqlalchemy import text as sqltext

    try:
        async with eng.begin() as conn:
            for s in stmts:
                await conn.execute(sqltext(s))
        logger.info("Async: PostgreSQL extensions ensured.")
    except Exception as e:
        logger.warning("Async ensure_extensions failed (non-critical): %s", e)


# -----------------------------------------------------------------------------
# Sync API (FastAPI dependency и утилиты)
# -----------------------------------------------------------------------------
class _RoutingSession(Session):
    def get_bind(self, mapper=None, clause=None, **kw):  # type: ignore[override]
        mode = _rw_mode.get()
        if mode == "read" and _SYNC_REPLICA_ENGINE is not None:
            return _SYNC_REPLICA_ENGINE
        return _get_sync_engine()


def get_db(use_routing: bool = False) -> Generator[Session, None, None]:
    eng = _get_sync_engine()
    maker = sessionmaker(
        bind=eng,
        class_=_RoutingSession if use_routing else Session,
        autocommit=False,
        autoflush=False,
        expire_on_commit=False,
    )
    db = maker()
    try:
        yield db
        db.commit()
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass
        raise
    finally:
        try:
            db.close()
        except Exception:
            pass


# ✅ Совместимый алиас для старых импортов
def get_session() -> Generator[Session, None, None]:
    yield from get_db()


@contextmanager
def session_scope(use_routing: bool = False) -> Iterator[Session]:
    eng = _get_sync_engine()
    maker = sessionmaker(
        bind=eng,
        class_=_RoutingSession if use_routing else Session,
        autocommit=False,
        autoflush=False,
        expire_on_commit=False,
    )
    db = maker()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def init_db(create_all: bool = True) -> None:
    raise RuntimeError("Database schema must be managed by Alembic, not create_all")


def drop_db(drop_all: bool = True) -> None:
    if not drop_all:
        return
    try:
        Base.metadata.drop_all(bind=_get_sync_engine())
        logger.info("DB schema dropped (sync).")
    except SQLAlchemyError as e:
        logger.exception("drop_all failed: %s", e)
        raise


def recreate_db() -> None:
    drop_db(True)
    init_db(True)


def dispose_engine() -> None:
    try:
        if _SYNC_REPLICA_ENGINE is not None:
            _SYNC_REPLICA_ENGINE.dispose()
        if _SYNC_ENGINE is not None:
            _SYNC_ENGINE.dispose()
        logger.info("Sync engines disposed")
    except Exception as e:
        logger.warning("dispose_engine failed: %s", e)


def reload_engine() -> None:
    global _SYNC_ENGINE, _SYNC_SESSION_MAKER, _SYNC_REPLICA_ENGINE
    dispose_engine()
    _SYNC_ENGINE = None
    _SYNC_REPLICA_ENGINE = None
    _SYNC_SESSION_MAKER = None
    _get_sync_engine()  # recreate (лениво, без коннекта)


def health_check_db(timeout_seconds: int = 2) -> dict:
    try:
        eng = _get_sync_engine()
        with eng.connect() as conn:
            conn.execution_options(timeout=timeout_seconds)
            conn.execute(text("SELECT 1"))
            ver = None
            try:
                ver = conn.execute(text("SHOW server_version")).scalar_one_or_none()
            except Exception:
                ver = None
        return {"ok": True, "error": None, "server_version": ver}
    except (OperationalError, SQLAlchemyError) as e:
        logger.error("DB health check failed: %s", e)
        return {"ok": False, "error": str(e), "server_version": None}


def ensure_extensions() -> None:
    stmts = [
        'CREATE EXTENSION IF NOT EXISTS "uuid-ossp"',
        "CREATE EXTENSION IF NOT EXISTS pg_trgm",
        "CREATE EXTENSION IF NOT EXISTS citext",
    ]
    try:
        eng = _get_sync_engine()
        with eng.begin() as conn:
            for s in stmts:
                conn.execute(text(s))
        logger.info("PostgreSQL extensions ensured (sync).")
    except Exception as e:
        logger.warning("Ensuring extensions failed (non-critical): %s", e)


# -----------------------------------------------------------------------------
# Alembic helper
# -----------------------------------------------------------------------------
def get_alembic_engine_url() -> str:
    """Правильный sync URL (postgresql+psycopg2://...) для Alembic env.py."""
    return _resolve_sync_pg_url()


# -----------------------------------------------------------------------------
# Best-effort OpenTelemetry instrumentation
# -----------------------------------------------------------------------------
def _try_instrument_otel() -> None:
    try:
        from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor  # type: ignore
    except Exception:
        return
    try:
        SQLAlchemyInstrumentor().instrument(
            engine=_get_sync_engine(),
            enable_commenter=True,
            commenter_options={"db_framework": "sqlalchemy-sync"},
        )
    except Exception as e:
        logger.debug("OTEL instrument (sync) skipped: %s", e)
    try:
        SQLAlchemyInstrumentor().instrument(
            engine=_get_async_engine().sync_engine,
            enable_commenter=True,
            commenter_options={"db_framework": "sqlalchemy-async"},
        )
    except Exception as e:
        logger.debug("OTEL instrument (async) skipped: %s", e)


_try_instrument_otel()
