# app/db/session.py
from __future__ import annotations

from collections.abc import AsyncGenerator, Generator

from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core import config
from app.core.db import _normalize_pg_to_asyncpg, _normalize_pg_to_psycopg2

resolved_url, _, _ = config.resolve_database_url(config.get_settings())
SYNC_URL = _normalize_pg_to_psycopg2(resolved_url)
ASYNC_URL = _normalize_pg_to_asyncpg(resolved_url)

# Синхронный движок (alembic/служебные задачи)
engine = create_engine(SYNC_URL, pool_pre_ping=True, future=True)
SessionLocal = sessionmaker(bind=engine, class_=Session, autocommit=False, autoflush=False, future=True)

# Асинхронный движок (ручки FastAPI)
async_engine = create_async_engine(ASYNC_URL, pool_pre_ping=True, future=True)
AsyncSessionLocal = sessionmaker(
    bind=async_engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
    future=True,
)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


async def get_async_session() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        yield session
