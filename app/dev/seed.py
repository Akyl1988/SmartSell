from __future__ import annotations

import logging
import os

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.db import get_async_db
from app.core.security import get_password_hash
from app.models.company import Company
from app.models.user import User

logger = logging.getLogger(__name__)


def _is_dev_env() -> bool:
    env_val = str(getattr(settings, "ENVIRONMENT", "") or "").lower()
    if env_val in {"development", "dev", "local", "test", "testing"}:
        return True
    return bool(getattr(settings, "DEBUG", False))


def _looks_like_email(value: str) -> bool:
    v = (value or "").strip()
    return bool(v and "@" in v and "." in v)


def _get_identifier() -> str | None:
    raw = os.getenv("SMARTSELL_IDENTIFIER") or ""
    ident = raw.strip()
    return ident or None


def _get_password() -> str | None:
    raw = os.getenv("SMARTSELL_PASSWORD") or ""
    pwd = raw.strip()
    return pwd or None


def _get_platform_identifier() -> str | None:
    raw = os.getenv("SMARTSELL_PLATFORM_ADMIN_IDENTIFIER") or ""
    ident = raw.strip()
    return ident or None


def _get_platform_password() -> str | None:
    raw = os.getenv("SMARTSELL_PLATFORM_ADMIN_PASSWORD") or ""
    pwd = raw.strip()
    return pwd or None


async def _ensure_company(session: AsyncSession, name: str) -> Company:
    res = await session.execute(select(Company).where(Company.name == name))
    company = res.scalars().first()
    if company:
        if not company.is_active:
            company.is_active = True
        return company

    company = Company(name=name, is_active=True, subscription_plan="start")
    session.add(company)
    await session.flush()
    return company


async def _ensure_user(
    session: AsyncSession,
    *,
    identifier: str,
    password: str,
    role: str,
    company: Company,
) -> User:
    if _looks_like_email(identifier):
        res = await session.execute(select(User).where(User.email == identifier))
        user = res.scalars().first()
        if not user:
            user = User(email=identifier)
            session.add(user)
    else:
        res = await session.execute(select(User).where(User.phone == identifier))
        user = res.scalars().first()
        if not user:
            user = User(phone=identifier)
            session.add(user)

    user.hashed_password = get_password_hash(password)
    user.role = role
    user.is_active = True
    user.is_verified = True
    user.company_id = company.id
    await session.flush()
    return user


async def ensure_dev_seed(*, session: AsyncSession | None = None) -> None:
    if not _is_dev_env():
        return

    if getattr(settings, "TESTING", False) and os.getenv("SMARTSELL_DEV_SEED", "").lower() not in {
        "1",
        "true",
        "yes",
        "on",
    }:
        return

    identifier = _get_identifier()
    password = _get_password()
    if not identifier or not password:
        logger.info("dev_seed: skipped (SMARTSELL_IDENTIFIER/SMARTSELL_PASSWORD not set)")
        return

    company_name = (os.getenv("SMARTSELL_COMPANY_NAME") or "Dev Company").strip() or "Dev Company"

    owns_session = False
    if session is None:
        owns_session = True
        async for s in get_async_db():
            session = s
            break

    if session is None:
        logger.warning("dev_seed: no db session available")
        return

    try:
        company = await _ensure_company(session, company_name)
        user = await _ensure_user(
            session,
            identifier=identifier,
            password=password,
            role="admin",
            company=company,
        )
        if not company.owner_id:
            company.owner_id = user.id

        platform_ident = _get_platform_identifier()
        platform_password = _get_platform_password()
        if platform_ident and platform_password:
            await _ensure_user(
                session,
                identifier=platform_ident,
                password=platform_password,
                role="platform_admin",
                company=company,
            )

        await session.commit()
        logger.info("dev_seed: ensured user/company")
    except Exception as exc:
        await session.rollback()
        logger.warning("dev_seed failed: %s", exc)
    finally:
        if owns_session:
            await session.close()
