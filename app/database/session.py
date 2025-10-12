# app/db/session.py
from __future__ import annotations

import os
from collections.abc import AsyncGenerator, Generator

from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import Session, sessionmaker

# Поддерживаем как postgresql+psycopg2://..., так и postgresql://...
SYNC_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+psycopg2://postgres:password@localhost:5432/smartsell",
).strip()

ASYNC_URL = os.getenv("ASYNC_DATABASE_URL", "").strip()
if not ASYNC_URL:
    if SYNC_URL.startswith("postgresql+psycopg2://"):
        ASYNC_URL = SYNC_URL.replace("postgresql+psycopg2://", "postgresql+asyncpg://", 1)
    elif SYNC_URL.startswith("postgresql://"):
        ASYNC_URL = SYNC_URL.replace("postgresql://", "postgresql+asyncpg://", 1)
    else:
        # крайний случай — оставим как есть (для других диалектов)
        ASYNC_URL = SYNC_URL

# Синхронный движок (alembic/служебные задачи)
engine = create_engine(SYNC_URL, pool_pre_ping=True, future=True)
SessionLocal = sessionmaker(
    bind=engine, class_=Session, autocommit=False, autoflush=False, future=True
)

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
