# ============================================
# app/models/kaspi_token.py — Kaspi store token
# ============================================
# -*- coding: utf-8 -*-
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional, List, Dict, Any

import sqlalchemy as sa
from sqlalchemy import String, text
from sqlalchemy.dialects.postgresql import UUID, BYTEA
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.core.config import settings


# --------------------------------------------
# Вспомогательные константы/утилиты уровня модуля
# --------------------------------------------

MASK_HEX_LEN_DEFAULT = 10
MASK_CHAR_DEFAULT = "…"


def _normalize_name(name: str) -> str:
    """Единая нормализация имени магазина: trim + lower."""
    return (name or "").strip().lower()


class KaspiStoreToken(Base):
    __tablename__ = "kaspi_store_tokens"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),  # pgcrypto/pg13+: gen_random_uuid()
    )
    store_name: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    # шифротекст токена (pgp_sym_encrypt), хранится в BYTEA
    token_ciphertext: Mapped[bytes] = mapped_column(BYTEA, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default=text("now()"), server_onupdate=text("now()")
    )

    # -----------------------
    # RAW-SQL хелперы (без ORM-мэппинга токена в явном виде)
    # -----------------------

    @classmethod
    async def upsert_token(
        cls,
        session: AsyncSession,
        store_name: str,
        plaintext_token: str,
    ) -> "KaspiStoreToken":
        """
        Идempotent upsert токена по имени магазина.
        - Имя нормализуется (trim/lower) при сравнении/конфликте.
        - В таблице поле store_name остаётся «как ввели» (для UX), но конфликт идёт по самому store_name.
          Поэтому рекомендуем хранить уже нормализованное значение (см. normalize_on_write=True ниже).
        """
        if not store_name or not plaintext_token:
            raise ValueError("store_name and token are required")

        enc_key = settings.get_kaspi_enc_key()
        store = store_name if settings is None else (
            store_name if not getattr(settings, "normalize_on_write", True)
            else _normalize_name(store_name)
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
        """
        Возвращает расшифрованный токен по имени магазина (регистронезависимо, с trim).
        """
        enc_key = settings.get_kaspi_enc_key()
        sql = sa.text(
            """
            SELECT convert_from(pgp_sym_decrypt(token_ciphertext, :k), 'UTF8') AS token
            FROM kaspi_store_tokens
            WHERE lower(trim(store_name)) = lower(trim(:store))
            LIMIT 1
            """
        )
        res = await session.execute(sql, {"store": store_name, "k": enc_key})
        row = res.fetchone()
        return (row[0] if row else None)

    @classmethod
    async def list_stores(cls, session: AsyncSession) -> List[str]:
        """
        Возвращает список имён магазинов (как хранятся в таблице), отсортированный по имени.
        """
        sql = sa.text("SELECT store_name FROM kaspi_store_tokens ORDER BY store_name")
        res = await session.execute(sql)
        return list(res.scalars().all())

    # -----------------------
    # Новые утилиты по ТЗ и для API
    # -----------------------

    @classmethod
    async def get_masked_card(
        cls,
        session: AsyncSession,
        store_name: str,
        *,
        mask_len: int = MASK_HEX_LEN_DEFAULT,
        mask_char: str = MASK_CHAR_DEFAULT,
    ) -> Optional[Dict[str, Any]]:
        """
        Возвращает «карточку» токена без раскрытия секрета:
        id, store_name, token_hex_masked, created_at, updated_at.
        Маскирование: первые mask_len hex-символов шифротекста + mask_char.
        """
        sql = sa.text(
            f"""
            SELECT
                id,
                store_name,
                left(encode(token_ciphertext,'hex'), {mask_len}) || :mask AS token_hex_masked,
                created_at,
                updated_at
            FROM kaspi_store_tokens
            WHERE lower(trim(store_name)) = lower(trim(:name))
            LIMIT 1
            """
        )
        res = await session.execute(sql, {"name": store_name, "mask": mask_char})
        row = res.mappings().first()
        return dict(row) if row else None

    @classmethod
    async def delete_by_store(cls, session: AsyncSession, store_name: str) -> bool:
        """
        Удаляет запись токена по имени магазина (регистронезависимо/trim).
        Возвращает True, если удалена хотя бы одна строка.
        """
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
        """
        Быстрая проверка существования записи по имени (регистронезависимо).
        """
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
        """
        Подсчёт числа записей.
        """
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
        """
        Переименовать store_name (с проверками).
        - Если normalize=True, применяем _normalize_name к new_name перед записью.
        - Не даём перезаписать существующую запись (конфликт уникальности).
        Возвращает True при успешном изменении.
        """
        target_name = _normalize_name(new_name) if normalize else new_name
        if not target_name:
            raise ValueError("new_name is empty after normalization")

        # Проверка конфликта
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
        """
        Ротация токена: безопасно заменяет шифротекст новым значением.
        Возвращает True, если запись была обновлена.
        """
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

    # -----------------------
    # Утилиты для схемы/миграций (не вызываются автоматически)
    # -----------------------

    @staticmethod
    def create_normalized_index_sql() -> str:
        """
        Рекомендуемый индекс для ускорения GET/DELETE/EXISTS по нормализованному имени.
        Применить через Alembic:
            op.execute(KaspiStoreToken.create_normalized_index_sql())
        """
        return (
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_kaspi_store_tokens_store_name_norm "
            "ON kaspi_store_tokens ((lower(trim(store_name))));"
        )
