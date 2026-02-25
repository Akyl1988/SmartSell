from __future__ import annotations

import logging
import os
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.db import get_async_session_maker
from app.core.security import get_password_hash
from app.models.company import Company
from app.models.subscription_catalog import Feature, Plan, PlanFeature
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


def _apply_platform_identity(user: User, identifier: str) -> None:
    ident = (identifier or "").strip()
    if not ident:
        return
    if "@" in ident:
        user.email = ident
    elif ident.isdigit():
        user.phone = ident
    else:
        user.email = f"{ident}@local.dev"


async def _ensure_platform_admin(
    session: AsyncSession,
    *,
    identifier: str,
    password: str,
) -> User:
    ident = (identifier or "").strip()
    user = None
    if "@" in ident:
        res = await session.execute(select(User).where(User.email == ident))
        user = res.scalars().first()
    elif ident.isdigit():
        res = await session.execute(select(User).where(User.phone == ident))
        user = res.scalars().first()
    else:
        alt_email = f"{ident}@local.dev"
        res = await session.execute(select(User).where(User.email == alt_email))
        user = res.scalars().first()

    if not user:
        res = await session.execute(select(User).where(User.role == "platform_admin"))
        for candidate in res.scalars().all():
            if not (candidate.email or candidate.phone):
                user = candidate
                break

    if not user:
        user = User()
        session.add(user)

    _apply_platform_identity(user, ident)
    user.hashed_password = get_password_hash(password)
    user.role = "platform_admin"
    user.is_active = True
    user.is_verified = True
    await session.flush()
    return user


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


async def _ensure_plan(
    session: AsyncSession,
    *,
    code: str,
    name: str,
    price: str,
    currency: str = "KZT",
    is_active: bool = True,
    trial_days_default: int = 14,
) -> Plan:
    normalized = (code or "").strip().lower()
    res = await session.execute(select(Plan).where(Plan.code == normalized))
    plan = res.scalars().first()
    if plan:
        plan.name = name
        plan.price = Decimal(str(price))
        plan.currency = currency
        plan.is_active = is_active
        plan.trial_days_default = trial_days_default
        await session.flush()
        return plan

    plan = Plan(
        code=normalized,
        name=name,
        price=Decimal(str(price)),
        currency=currency,
        is_active=is_active,
        trial_days_default=trial_days_default,
    )
    session.add(plan)
    await session.flush()
    return plan


async def _ensure_feature(
    session: AsyncSession,
    *,
    code: str,
    name: str,
    description: str | None = None,
    is_active: bool = True,
) -> Feature:
    normalized = (code or "").strip().lower()
    res = await session.execute(select(Feature).where(Feature.code == normalized))
    feature = res.scalars().first()
    if feature:
        feature.name = name
        feature.description = description
        feature.is_active = is_active
        await session.flush()
        return feature

    feature = Feature(
        code=normalized,
        name=name,
        description=description,
        is_active=is_active,
    )
    session.add(feature)
    await session.flush()
    return feature


async def _ensure_plan_feature(
    session: AsyncSession,
    *,
    plan: Plan,
    feature: Feature,
    enabled: bool,
    limits: dict | None = None,
) -> PlanFeature:
    res = await session.execute(
        select(PlanFeature).where(
            PlanFeature.plan_id == plan.id,
            PlanFeature.feature_id == feature.id,
        )
    )
    plan_feature = res.scalars().first()
    if plan_feature:
        plan_feature.enabled = enabled
        plan_feature.limits_json = limits
        await session.flush()
        return plan_feature

    plan_feature = PlanFeature(
        plan_id=plan.id,
        feature_id=feature.id,
        enabled=enabled,
        limits_json=limits,
    )
    session.add(plan_feature)
    await session.flush()
    return plan_feature


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

    async def _run_seed(target_session: AsyncSession) -> None:
        company = await _ensure_company(target_session, company_name)
        user = await _ensure_user(
            target_session,
            identifier=identifier,
            password=password,
            role="admin",
            company=company,
        )
        if not company.owner_id:
            company.owner_id = user.id

        basic = await _ensure_plan(
            target_session,
            code="basic",
            name="Basic",
            price="0.00",
            currency="KZT",
            is_active=True,
            trial_days_default=14,
        )
        pro = await _ensure_plan(
            target_session,
            code="pro",
            name="Pro",
            price="0.00",
            currency="KZT",
            is_active=True,
            trial_days_default=15,
        )
        repricing = await _ensure_feature(
            target_session,
            code="repricing",
            name="Repricing",
            description="Dynamic pricing and repricing rules",
            is_active=True,
        )
        preorders = await _ensure_feature(
            target_session,
            code="preorders",
            name="Preorders",
            description="Preorders flow and conversion",
            is_active=True,
        )
        await _ensure_plan_feature(target_session, plan=pro, feature=repricing, enabled=True, limits={})
        await _ensure_plan_feature(target_session, plan=pro, feature=preorders, enabled=True, limits={})
        await _ensure_plan_feature(target_session, plan=basic, feature=repricing, enabled=False, limits={})
        await _ensure_plan_feature(target_session, plan=basic, feature=preorders, enabled=False, limits={})

        platform_ident = _get_platform_identifier()
        platform_password = _get_platform_password()
        if platform_ident and platform_password:
            await _ensure_platform_admin(
                target_session,
                identifier=platform_ident,
                password=platform_password,
            )
            logger.info("dev_seed: ensured platform_admin user identifier=%s", platform_ident)

    if session is not None:
        try:
            await _run_seed(session)
            await session.commit()
            logger.info("dev_seed: ensured user/company")
        except Exception as exc:
            await session.rollback()
            logger.warning("dev_seed failed: %s", exc)
        return

    maker = get_async_session_maker()
    async with maker() as local_session:
        try:
            await _run_seed(local_session)
            await local_session.commit()
            logger.info("dev_seed: ensured user/company")
        except Exception as exc:
            await local_session.rollback()
            logger.warning("dev_seed failed: %s", exc)
