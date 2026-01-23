from __future__ import annotations

from datetime import datetime

import sqlalchemy as sa
from sqlalchemy import Boolean, Column, DateTime, ForeignKey, String, Text, text
from sqlalchemy.dialects.postgresql import BYTEA, UUID
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.base import Base


class KaspiMcSession(Base):
    __tablename__ = "kaspi_mc_sessions"

    id = Column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    company_id = Column(ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True)
    merchant_uid = Column(String(128), nullable=False, index=True)
    cookies_ciphertext = Column(BYTEA, nullable=False)
    is_active = Column(Boolean, nullable=False, server_default=sa.text("true"))

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    last_used_at = Column(DateTime, nullable=True)
    last_error = Column(Text, nullable=True)

    __table_args__ = (sa.UniqueConstraint("company_id", "merchant_uid", name="uq_kaspi_mc_sessions_company_merchant"),)

    @classmethod
    async def upsert_session(
        cls,
        session: AsyncSession,
        *,
        company_id: int,
        merchant_uid: str,
        cookies: str,
        is_active: bool = True,
    ) -> KaspiMcSession:
        if not merchant_uid or not cookies:
            raise ValueError("merchant_uid and cookies are required")

        bind = session.get_bind()
        dialect = bind.dialect.name if bind is not None else ""
        if dialect == "sqlite":
            sql = sa.text(
                """
                INSERT INTO kaspi_mc_sessions (company_id, merchant_uid, cookies_ciphertext, is_active)
                VALUES (:company_id, :merchant_uid, :cookies, :is_active)
                ON CONFLICT (company_id, merchant_uid) DO UPDATE
                  SET cookies_ciphertext = EXCLUDED.cookies_ciphertext,
                      is_active = EXCLUDED.is_active,
                      last_error = NULL,
                      updated_at = CURRENT_TIMESTAMP
                RETURNING id, company_id, merchant_uid, cookies_ciphertext, is_active, created_at, updated_at, last_used_at, last_error
                """
            )
            params = {
                "company_id": company_id,
                "merchant_uid": merchant_uid,
                "cookies": cookies.encode("utf-8"),
                "is_active": is_active,
            }
        else:
            enc_key = settings.get_kaspi_enc_key()
            sql = sa.text(
                """
                INSERT INTO kaspi_mc_sessions (company_id, merchant_uid, cookies_ciphertext, is_active)
                VALUES (:company_id, :merchant_uid, pgp_sym_encrypt(:cookies, :k), :is_active)
                ON CONFLICT (company_id, merchant_uid) DO UPDATE
                  SET cookies_ciphertext = EXCLUDED.cookies_ciphertext,
                      is_active = EXCLUDED.is_active,
                      last_error = NULL,
                      updated_at = now()
                RETURNING id, company_id, merchant_uid, cookies_ciphertext, is_active, created_at, updated_at, last_used_at, last_error
                """
            )
            params = {
                "company_id": company_id,
                "merchant_uid": merchant_uid,
                "cookies": cookies,
                "is_active": is_active,
                "k": enc_key,
            }

        res = await session.execute(sql, params)
        row = res.fetchone()
        await session.commit()

        obj = cls()
        (
            obj.id,
            obj.company_id,
            obj.merchant_uid,
            obj.cookies_ciphertext,
            obj.is_active,
            obj.created_at,
            obj.updated_at,
            obj.last_used_at,
            obj.last_error,
        ) = row
        return obj

    @classmethod
    async def get_cookies(
        cls,
        session: AsyncSession,
        *,
        company_id: int,
        merchant_uid: str,
    ) -> str | None:
        bind = session.get_bind()
        dialect = bind.dialect.name if bind is not None else ""
        if dialect == "sqlite":
            sql = sa.text(
                """
                SELECT cookies_ciphertext AS cookies
                FROM kaspi_mc_sessions
                WHERE company_id = :company_id
                  AND merchant_uid = :merchant_uid
                  AND is_active = true
                LIMIT 1
                """
            )
            params = {"company_id": company_id, "merchant_uid": merchant_uid}
        else:
            enc_key = settings.get_kaspi_enc_key()
            sql = sa.text(
                """
                SELECT pgp_sym_decrypt(cookies_ciphertext, :k) AS cookies
                FROM kaspi_mc_sessions
                WHERE company_id = :company_id
                  AND merchant_uid = :merchant_uid
                  AND is_active = true
                LIMIT 1
                """
            )
            params = {"company_id": company_id, "merchant_uid": merchant_uid, "k": enc_key}

        res = await session.execute(sql, params)
        row = res.fetchone()
        if not row:
            return None
        cookies = row[0]
        if isinstance(cookies, bytes | bytearray):
            return cookies.decode("utf-8")
        return str(cookies)
