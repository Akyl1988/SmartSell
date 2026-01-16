"""
OTP (One-Time Password) utilities for phone verification.
"""

import hashlib
import hmac
import random
import re
from datetime import datetime, timedelta

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger
from app.models import OtpAttempt

logger = get_logger(__name__)

DEFAULT_OTP_TTL_MINUTES = 5


def generate_otp_code(length: int = 6) -> str:
    """Generate random OTP code"""
    return "".join([str(random.randint(0, 9)) for _ in range(length)])


def hash_otp_code(code: str) -> str:
    """Hash OTP code with secret key"""
    return hmac.new(settings.SECRET_KEY.encode("utf-8"), code.encode("utf-8"), hashlib.sha256).hexdigest()


def verify_otp_hash(code: str, code_hash: str) -> bool:
    """Verify OTP code against hash"""
    expected_hash = hash_otp_code(code)
    return hmac.compare_digest(expected_hash, code_hash)


def _phone_variants(phone: str) -> list[str]:
    """Return normalized variants for phone matching (digits, +digits, raw)."""
    raw = (phone or "").strip()
    digits = re.sub(r"\D", "", raw)
    variants: list[str] = []
    for val in (raw, digits, f"+{digits}"):
        if val and val not in variants:
            variants.append(val)
    return variants


async def create_otp_attempt(
    db: AsyncSession,
    phone: str,
    purpose: str = "login",
    expires_minutes: int = DEFAULT_OTP_TTL_MINUTES,
    attempts_left: int | None = None,
    code: str | None = None,
    user_id: int | None = None,
) -> tuple[str, OtpAttempt]:
    """Create new OTP attempt and return code."""

    otp_code = (code or "").strip() or generate_otp_code()
    code_hash = hash_otp_code(otp_code)

    otp_attempt = OtpAttempt.create_new(
        phone=phone,
        code_hash=code_hash,
        purpose=purpose,
        expires_minutes=expires_minutes,
        attempts_left=attempts_left if attempts_left is not None else 5,
        user_id=user_id,
    )

    db.add(otp_attempt)
    await db.commit()
    await db.refresh(otp_attempt)

    logger.info(f"OTP attempt created for {phone}, purpose: {purpose}")
    return otp_code, otp_attempt


async def verify_otp_code(db: AsyncSession, phone: str, code: str, purpose: str = "login") -> bool:
    """Verify OTP code for phone number."""

    variants = _phone_variants(phone)
    if not variants:
        return False

    try:
        result = await db.execute(
            select(OtpAttempt)
            .where(
                and_(
                    OtpAttempt.phone.in_(variants),
                    OtpAttempt.purpose == purpose,
                    OtpAttempt.expires_at > datetime.utcnow(),
                    OtpAttempt.is_verified.is_(False),
                    OtpAttempt.attempts_left > 0,
                    OtpAttempt.deleted_at.is_(None),
                )
            )
            .order_by(OtpAttempt.created_at.desc())
            .limit(1)
        )

        otp_attempt = result.scalar_one_or_none()

        if not otp_attempt:
            logger.warning(f"No valid OTP attempt found for {phone}")
            return False

        if not otp_attempt.is_valid():
            logger.warning(f"OTP attempt expired or blocked for {phone}")
            return False

        if not otp_attempt.use_attempt():
            logger.warning(f"No attempts left for OTP {phone}")
            await db.commit()
            return False

        if verify_otp_hash(code, otp_attempt.code_hash):
            otp_attempt.verify()
            await db.commit()

            logger.info(f"OTP verified successfully for {phone}")
            return True

        await db.commit()
        logger.warning(f"Invalid OTP code for {phone}")
        return False

    except Exception as e:
        logger.error(f"OTP verification error for {phone}: {e}")
        return False


async def check_rate_limits(db: AsyncSession, phone: str, ip_address: str = None) -> dict:
    """Check SMS rate limits for phone and IP"""

    now = datetime.utcnow()
    hour_ago = now - timedelta(hours=1)
    day_ago = now - timedelta(days=1)

    # Check phone limits
    result = await db.execute(
        select(OtpAttempt).where(and_(OtpAttempt.phone == phone, OtpAttempt.created_at >= hour_ago))
    )
    phone_hour_count = len(result.scalars().all())

    result = await db.execute(
        select(OtpAttempt).where(and_(OtpAttempt.phone == phone, OtpAttempt.created_at >= day_ago))
    )
    phone_day_count = len(result.scalars().all())

    # Rate limits
    max_per_hour = 5
    max_per_day = 10

    limits = {
        "phone_hour_count": phone_hour_count,
        "phone_day_count": phone_day_count,
        "phone_hour_limit": max_per_hour,
        "phone_day_limit": max_per_day,
        "phone_hour_exceeded": phone_hour_count >= max_per_hour,
        "phone_day_exceeded": phone_day_count >= max_per_day,
        "can_send": phone_hour_count < max_per_hour and phone_day_count < max_per_day,
    }

    return limits


async def cleanup_expired_otp(db: AsyncSession) -> int:
    """Clean up expired OTP attempts"""

    try:
        # Get expired OTP attempts
        result = await db.execute(select(OtpAttempt).where(OtpAttempt.expires_at < datetime.utcnow()))
        expired_attempts = result.scalars().all()

        # Delete expired attempts
        for attempt in expired_attempts:
            await db.delete(attempt)

        await db.commit()

        logger.info(f"Cleaned up {len(expired_attempts)} expired OTP attempts")
        return len(expired_attempts)

    except Exception as e:
        logger.error(f"OTP cleanup error: {e}")
        return 0


def format_phone_for_otp(phone: str) -> str:
    """Format phone number for OTP"""

    # Remove all non-digit characters except +
    phone = "".join(c for c in phone if c.isdigit() or c == "+")

    # Remove + if present
    if phone.startswith("+"):
        phone = phone[1:]

    # Ensure Kazakhstan format: 7XXXXXXXXXX
    if phone.startswith("8") and len(phone) == 11:
        phone = "7" + phone[1:]  # Convert 8XXXXXXXXXX to 7XXXXXXXXXX
    elif phone.startswith("77") and len(phone) == 11:
        phone = "7" + phone[2:]  # Remove duplicate 7
    elif len(phone) == 10:
        phone = "7" + phone  # Add country code

    # Add + for international format
    return "+" + phone if phone.startswith("7") and len(phone) == 11 else phone


def validate_otp_code(code: str) -> bool:
    """Validate OTP code format"""
    return code.isdigit() and len(code) == 6


async def get_otp_attempts_count(db: AsyncSession, phone: str, minutes: int = 60) -> int:
    """Get OTP attempts count for phone in last N minutes"""

    since = datetime.utcnow() - timedelta(minutes=minutes)

    result = await db.execute(select(OtpAttempt).where(and_(OtpAttempt.phone == phone, OtpAttempt.created_at >= since)))

    return len(result.scalars().all())
