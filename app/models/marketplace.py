# app/models/marketplace.py
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Optional

import sqlalchemy as sa
from sqlalchemy import BigInteger, Boolean, DateTime, Numeric, String, text
from sqlalchemy.dialects.postgresql import BYTEA, UUID
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.core.config import settings
from app.core.db import Base


def _normalize_name(name: str) -> str:
    return (name or "").strip().lower()


class KaspiStoreToken(Base):
    __tablename__ = "kaspi_store_tokens"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    store_name: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    token_ciphertext: Mapped[bytes] = mapped_column(BYTEA, nullable=False)

    created_at: Mapped[datetime] = mapped_column(nullable=False, server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default=text("now()"), server_onupdate=text("now()")
    )
    last_selftest_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_selftest_status: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_selftest_error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_selftest_error_message: Mapped[str | None] = mapped_column(String(500), nullable=True)

    @classmethod
    async def upsert_token(
        cls,
        session: AsyncSession,
        store_name: str,
        plaintext_token: str,
    ) -> KaspiStoreToken:
        if not store_name or not plaintext_token:
            raise ValueError("store_name and token are required")

        enc_key = settings.get_kaspi_enc_key()
        store = (
            store_name
            if settings is None
            else (store_name if not getattr(settings, "normalize_on_write", True) else _normalize_name(store_name))
        )

        sql = sa.text(
            """
            INSERT INTO kaspi_store_tokens (store_name, token_ciphertext)
            VALUES (:store, pgp_sym_encrypt(:tok, :k))
            ON CONFLICT (store_name) DO UPDATE
              SET token_ciphertext = EXCLUDED.token_ciphertext,
                  updated_at = now()
            RETURNING id, store_name, token_ciphertext, created_at, updated_at
            """
        )
        try:
            res = await session.execute(sql, {"store": store, "tok": plaintext_token, "k": enc_key})
            row = res.fetchone()
            await session.commit()
        except Exception:
            await session.rollback()
            raise

        obj = cls()
        obj.id, obj.store_name, obj.token_ciphertext, obj.created_at, obj.updated_at = row
        return obj

    @classmethod
    async def get_token(cls, session: AsyncSession, store_name: str) -> Optional[str]:
        enc_key = settings.get_kaspi_enc_key()
        sql = sa.text(
            """
            SELECT pgp_sym_decrypt(token_ciphertext, :k) AS token
            FROM kaspi_store_tokens
            WHERE lower(trim(store_name)) = lower(trim(:store))
            LIMIT 1
            """
        )
        res = await session.execute(sql, {"store": store_name, "k": enc_key})
        row = res.fetchone()
        if not row:
            return None
        token = row[0]
        if isinstance(token, bytes | bytearray):
            return token.decode("utf-8")
        return str(token)

    @classmethod
    async def list_stores(cls, session: AsyncSession) -> list[str]:
        sql = sa.text("SELECT store_name FROM kaspi_store_tokens ORDER BY store_name")
        res = await session.execute(sql)
        return list(res.scalars().all())

    @classmethod
    async def get_masked_card(
        cls,
        session: AsyncSession,
        store_name: str,
        *,
        mask_len: int = 10,
        mask_char: str = "…",
    ) -> Optional[dict[str, Any]]:
        sql = sa.text(
            f"""
            SELECT
                id,
                store_name,
                left(encode(token_ciphertext,'hex'), {mask_len}) || :mask AS token_hex_masked,
                created_at,
                updated_at,
                last_selftest_at,
                last_selftest_status,
                last_selftest_error_code,
                last_selftest_error_message
            FROM kaspi_store_tokens
            WHERE lower(trim(store_name)) = lower(trim(:name))
            LIMIT 1
            """
        )
        res = await session.execute(sql, {"name": store_name, "mask": mask_char})
        row = res.mappings().first()
        return dict(row) if row else None

    @classmethod
    async def update_selftest(
        cls,
        session: AsyncSession,
        store_name: str,
        status: str,
        *,
        error_code: str | None = None,
        error_message: str | None = None,
        occurred_at: datetime | None = None,
        commit: bool = True,
    ) -> None:
        if not store_name:
            return
        when = occurred_at or datetime.utcnow()
        msg = (error_message or "").strip()
        if msg:
            msg = msg[:500]

        sql = sa.text(
            """
            UPDATE kaspi_store_tokens
            SET
                last_selftest_at = :when,
                last_selftest_status = :status,
                last_selftest_error_code = :error_code,
                last_selftest_error_message = :error_message,
                updated_at = now()
            WHERE lower(trim(store_name)) = lower(trim(:name))
            """
        )
        try:
            await session.execute(
                sql,
                {
                    "when": when,
                    "status": status,
                    "error_code": error_code,
                    "error_message": msg or None,
                    "name": store_name,
                },
            )
            if commit:
                await session.commit()
        except Exception:
            if commit:
                await session.rollback()
            raise

    @classmethod
    async def delete_by_store(cls, session: AsyncSession, store_name: str) -> bool:
        sql = sa.text(
            """
            DELETE FROM kaspi_store_tokens
            WHERE lower(trim(store_name)) = lower(trim(:name))
            """
        )
        try:
            res = await session.execute(sql, {"name": store_name})
            await session.commit()
            return (res.rowcount or 0) > 0
        except Exception:
            await session.rollback()
            raise

    @classmethod
    async def exists(cls, session: AsyncSession, store_name: str) -> bool:
        sql = sa.text(
            """
            SELECT 1
            FROM kaspi_store_tokens
            WHERE lower(trim(store_name)) = lower(trim(:name))
            LIMIT 1
            """
        )
        res = await session.execute(sql, {"name": store_name})
        return res.scalar_one_or_none() is not None

    @classmethod
    async def count(cls, session: AsyncSession) -> int:
        sql = sa.text("SELECT count(*) FROM kaspi_store_tokens")
        res = await session.execute(sql)
        return int(res.scalar_one())

    @classmethod
    async def rename_store(
        cls,
        session: AsyncSession,
        old_name: str,
        new_name: str,
        *,
        normalize: bool = True,
    ) -> bool:
        target_name = _normalize_name(new_name) if normalize else new_name
        if not target_name:
            raise ValueError("new_name is empty after normalization")

        if await cls.exists(session, target_name):
            raise ValueError("target store_name already exists")

        sql = sa.text(
            """
            UPDATE kaspi_store_tokens
            SET store_name = :new_name,
                updated_at = now()
            WHERE lower(trim(store_name)) = lower(trim(:old_name))
            """
        )
        try:
            res = await session.execute(sql, {"new_name": target_name, "old_name": old_name})
            await session.commit()
            return (res.rowcount or 0) > 0
        except Exception:
            await session.rollback()
            raise

    @classmethod
    async def rotate_token(
        cls,
        session: AsyncSession,
        store_name: str,
        new_plaintext_token: str,
    ) -> bool:
        if not new_plaintext_token:
            raise ValueError("new token is empty")

        enc_key = settings.get_kaspi_enc_key()
        sql = sa.text(
            """
            UPDATE kaspi_store_tokens
            SET token_ciphertext = pgp_sym_encrypt(:tok, :k),
                updated_at = now()
            WHERE lower(trim(store_name)) = lower(trim(:name))
            """
        )
        try:
            res = await session.execute(sql, {"tok": new_plaintext_token, "k": enc_key, "name": store_name})
            await session.commit()
            return (res.rowcount or 0) > 0
        except Exception:
            await session.rollback()
            raise

    @staticmethod
    def create_normalized_index_sql() -> str:
        return (
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_kaspi_store_tokens_store_name_norm "
            "ON kaspi_store_tokens ((lower(trim(store_name))));"
        )


class ProductMarketplacePrice(Base):
    __tablename__ = "product_marketplace_price"
    __table_args__ = ()  # Removed schema='public' for SQLite compatibility

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    product_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    marketplace: Mapped[str] = mapped_column(String(32), nullable=False)
    price: Mapped[str] = mapped_column(Numeric(14, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="KZT", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[str] = mapped_column(default=func.now(), nullable=False)
    updated_at: Mapped[str] = mapped_column(default=func.now(), onupdate=func.now(), nullable=False)
