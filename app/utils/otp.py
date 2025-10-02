"""
OTP (One-Time Password) utilities for phone verification.
"""

import hashlib
import hmac
import random
from datetime import datetime, timedelta

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger
from app.models import OtpAttempt

logger = get_logger(__name__)


def generate_otp_code(length: int = 6) -> str:
    """Generate random OTP code"""
    return "".join([str(random.randint(0, 9)) for _ in range(length)])


def hash_otp_code(code: str) -> str:
    """Hash OTP code with secret key"""
    return hmac.new(
        settings.SECRET_KEY.encode("utf-8"), code.encode("utf-8"), hashlib.sha256
    ).hexdigest()


def verify_otp_hash(code: str, code_hash: str) -> bool:
    """Verify OTP code against hash"""
    expected_hash = hash_otp_code(code)
    return hmac.compare_digest(expected_hash, code_hash)


async def create_otp_attempt(
    db: AsyncSession, phone: str, purpose: str = "login", expires_minutes: int = 5
) -> tuple[str, OtpAttempt]:
    """Create new OTP attempt and return code"""

    # Generate OTP code
    otp_code = generate_otp_code()
    code_hash = hash_otp_code(otp_code)

    # Create OTP attempt
    otp_attempt = OtpAttempt.create_new(
        phone=phone,
        code_hash=code_hash,
        purpose=purpose,
        expires_minutes=expires_minutes,
    )

    db.add(otp_attempt)
    await db.commit()
    await db.refresh(otp_attempt)

    logger.info(f"OTP attempt created for {phone}, purpose: {purpose}")
    return otp_code, otp_attempt


async def verify_otp_code(db: AsyncSession, phone: str, code: str, purpose: str = "login") -> bool:
    """Verify OTP code for phone number"""

    try:
        # Get latest valid OTP attempt
        result = await db.execute(
            select(OtpAttempt)
            .where(
                and_(
                    OtpAttempt.phone == phone,
                    OtpAttempt.purpose == purpose,
                    OtpAttempt.expires_at > datetime.utcnow(),
                    not OtpAttempt.is_verified,
                    OtpAttempt.attempts_left > 0,
                )
            )
            .order_by(OtpAttempt.created_at.desc())
            .limit(1)
        )

        otp_attempt = result.scalar_one_or_none()

        if not otp_attempt:
            logger.warning(f"No valid OTP attempt found for {phone}")
            return False

        # Check if OTP is still valid
        if not otp_attempt.is_valid:
            logger.warning(f"OTP attempt expired or used for {phone}")
            return False

        # Use one attempt
        if not otp_attempt.use_attempt():
            logger.warning(f"No attempts left for OTP {phone}")
            await db.commit()
            return False

        # Verify code
        if verify_otp_hash(code, otp_attempt.code_hash):
            # Mark as verified
            otp_attempt.verify()
            await db.commit()

            logger.info(f"OTP verified successfully for {phone}")
            return True
        else:
            # Wrong code, save attempt count
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
        result = await db.execute(
            select(OtpAttempt).where(OtpAttempt.expires_at < datetime.utcnow())
        )
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

    result = await db.execute(
        select(OtpAttempt).where(and_(OtpAttempt.phone == phone, OtpAttempt.created_at >= since))
    )

    return len(result.scalars().all())
