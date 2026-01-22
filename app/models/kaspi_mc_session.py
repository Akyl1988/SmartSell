from __future__ import annotations

from datetime import datetime

import sqlalchemy as sa
from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import BYTEA, UUID
from sqlalchemy.exc import ProgrammingError
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
    x_auth_version = Column(Integer, nullable=False, server_default=sa.text("3"))
    comment = Column(Text, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    last_used_at = Column(DateTime, nullable=True)
    last_error = Column(Text, nullable=True)
    last_error_code = Column(String(64), nullable=True)
    last_error_at = Column(DateTime, nullable=True)
    revoked_at = Column(DateTime, nullable=True)

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
        x_auth_version: int = 3,
        comment: str | None = None,
    ) -> KaspiMcSession:
        if not merchant_uid or not cookies:
            raise ValueError("merchant_uid and cookies are required")

        bind = session.get_bind()
        dialect = bind.dialect.name if bind is not None else ""
        if dialect == "sqlite":
            sql = sa.text(
                """
                INSERT INTO kaspi_mc_sessions (
                    company_id,
                    merchant_uid,
                    cookies_ciphertext,
                    is_active,
                    x_auth_version,
                    comment,
                    created_at,
                    updated_at
                )
                VALUES (
                    :company_id,
                    :merchant_uid,
                    :cookies,
                    :is_active,
                    :x_auth_version,
                    :comment,
                    CURRENT_TIMESTAMP,
                    CURRENT_TIMESTAMP
                )
                ON CONFLICT (company_id, merchant_uid) DO UPDATE
                  SET cookies_ciphertext = EXCLUDED.cookies_ciphertext,
                      is_active = EXCLUDED.is_active,
                      x_auth_version = EXCLUDED.x_auth_version,
                      comment = EXCLUDED.comment,
                      revoked_at = NULL,
                      last_error = NULL,
                      last_error_code = NULL,
                      last_error_at = NULL,
                      updated_at = CURRENT_TIMESTAMP
                RETURNING id, company_id, merchant_uid, cookies_ciphertext, is_active, x_auth_version, comment,
                          created_at, updated_at, last_used_at, last_error, last_error_code, last_error_at, revoked_at
                """
            )
            params = {
                "company_id": company_id,
                "merchant_uid": merchant_uid,
                "cookies": cookies.encode("utf-8"),
                "is_active": is_active,
                "x_auth_version": x_auth_version,
                "comment": comment,
            }
        else:
            enc_key = settings.get_kaspi_enc_key()
            sql = sa.text(
                """
                INSERT INTO kaspi_mc_sessions (
                    company_id,
                    merchant_uid,
                    cookies_ciphertext,
                    is_active,
                    x_auth_version,
                    comment,
                    created_at,
                    updated_at
                )
                VALUES (
                    :company_id,
                    :merchant_uid,
                    pgp_sym_encrypt(:cookies, :k),
                    :is_active,
                    :x_auth_version,
                    :comment,
                    now(),
                    now()
                )
                ON CONFLICT (company_id, merchant_uid) DO UPDATE
                  SET cookies_ciphertext = EXCLUDED.cookies_ciphertext,
                      is_active = EXCLUDED.is_active,
                      x_auth_version = EXCLUDED.x_auth_version,
                      comment = EXCLUDED.comment,
                      revoked_at = NULL,
                      last_error = NULL,
                      last_error_code = NULL,
                      last_error_at = NULL,
                      updated_at = now()
                RETURNING id, company_id, merchant_uid, cookies_ciphertext, is_active, x_auth_version, comment,
                          created_at, updated_at, last_used_at, last_error, last_error_code, last_error_at, revoked_at
                """
            )
            params = {
                "company_id": company_id,
                "merchant_uid": merchant_uid,
                "cookies": cookies,
                "is_active": is_active,
                "x_auth_version": x_auth_version,
                "comment": comment,
                "k": enc_key,
            }

        try:
            res = await session.execute(sql, params)
            row = res.fetchone()
            await session.commit()
        except ProgrammingError as exc:
            await session.rollback()
            if "pgp_sym_encrypt" not in str(exc):
                raise
            fallback_sql = sa.text(
                """
                INSERT INTO kaspi_mc_sessions (
                    company_id,
                    merchant_uid,
                    cookies_ciphertext,
                    is_active,
                    x_auth_version,
                    comment,
                    created_at,
                    updated_at
                )
                VALUES (
                    :company_id,
                    :merchant_uid,
                    :cookies,
                    :is_active,
                    :x_auth_version,
                    :comment,
                    now(),
                    now()
                )
                ON CONFLICT (company_id, merchant_uid) DO UPDATE
                  SET cookies_ciphertext = EXCLUDED.cookies_ciphertext,
                      is_active = EXCLUDED.is_active,
                      x_auth_version = EXCLUDED.x_auth_version,
                      comment = EXCLUDED.comment,
                      revoked_at = NULL,
                      last_error = NULL,
                      last_error_code = NULL,
                      last_error_at = NULL,
                      updated_at = now()
                RETURNING id, company_id, merchant_uid, cookies_ciphertext, is_active, x_auth_version, comment,
                          created_at, updated_at, last_used_at, last_error, last_error_code, last_error_at, revoked_at
                """
            )
            fallback_params = {
                "company_id": company_id,
                "merchant_uid": merchant_uid,
                "cookies": cookies.encode("utf-8"),
                "is_active": is_active,
                "x_auth_version": x_auth_version,
                "comment": comment,
            }
            res = await session.execute(fallback_sql, fallback_params)
            row = res.fetchone()
            await session.commit()

        obj = cls()
        (
            obj.id,
            obj.company_id,
            obj.merchant_uid,
            obj.cookies_ciphertext,
            obj.is_active,
            obj.x_auth_version,
            obj.comment,
            obj.created_at,
            obj.updated_at,
            obj.last_used_at,
            obj.last_error,
            obj.last_error_code,
            obj.last_error_at,
            obj.revoked_at,
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

        try:
            res = await session.execute(sql, params)
            row = res.fetchone()
        except ProgrammingError as exc:
            await session.rollback()
            if "pgp_sym_decrypt" not in str(exc):
                raise
            fallback_sql = sa.text(
                """
                SELECT cookies_ciphertext AS cookies
                FROM kaspi_mc_sessions
                WHERE company_id = :company_id
                  AND merchant_uid = :merchant_uid
                  AND is_active = true
                LIMIT 1
                """
            )
            res = await session.execute(fallback_sql, {"company_id": company_id, "merchant_uid": merchant_uid})
            row = res.fetchone()
        if not row:
            return None
        cookies = row[0]
        if isinstance(cookies, bytes | bytearray):
            return cookies.decode("utf-8")
        return str(cookies)
