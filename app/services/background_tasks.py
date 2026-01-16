"""
Background task services using Celery for async operations.
"""

from __future__ import annotations

import os
import re

from celery import Celery

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


def _mask_phone(value: str) -> str:
    digits = re.sub(r"\D", "", value or "")
    if not digits:
        return ""
    if len(digits) <= 4:
        return "*" * len(digits)
    return "*" * (len(digits) - 2) + digits[-2:]


def _should_log_otp_debug() -> bool:
    env = str(os.getenv("ENVIRONMENT") or getattr(settings, "ENVIRONMENT", "production") or "production").lower()
    return bool(getattr(settings, "DEBUG_OTP_LOGGING", False)) and env == "development"


# Initialize Celery
celery_app = Celery(
    "smartsell",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
    include=["app.services.background_tasks"],
)

# Configure Celery
celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_reject_on_worker_lost=True,
    result_expires=3600,
)


@celery_app.task
def send_sms_otp(phone: str, code: str) -> bool:
    """Send SMS OTP code using Mobizon API."""
    try:
        # TODO: Implement actual Mobizon API call
        logger.info(f"Sending OTP to {_mask_phone(phone)}")

        # Simulate API call delay
        import time

        time.sleep(1)

        if _should_log_otp_debug():
            logger.info(f"OTP requested for phone {_mask_phone(phone)}")

        return True
    except Exception as e:
        logger.error(f"Failed to send OTP to {phone}: {e}")
        return False


@celery_app.task
def send_email_notification(email: str, subject: str, body: str) -> bool:
    """Send email notification."""
    try:
        # TODO: Implement email sending
        logger.info(f"Sending email to {email}: {subject}")

        # Simulate email sending
        import time

        time.sleep(0.5)

        return True
    except Exception as e:
        logger.error(f"Failed to send email to {email}: {e}")
        return False


@celery_app.task
def process_image_upload(image_url: str, product_id: int) -> dict:
    """Process image upload to Cloudinary."""
    try:
        # TODO: Implement Cloudinary upload
        logger.info(f"Processing image upload for product {product_id}")

        # Simulate image processing
        import time

        time.sleep(2)

        # Return processed image URLs
        return {
            "original": image_url,
            "thumbnail": f"{image_url}?w=200&h=200",
            "medium": f"{image_url}?w=500&h=500",
            "large": f"{image_url}?w=1000&h=1000",
        }
    except Exception as e:
        logger.error(f"Failed to process image for product {product_id}: {e}")
        return {"error": str(e)}


@celery_app.task
def sync_product_to_kaspi(product_id: int) -> bool:
    """Sync product to Kaspi marketplace."""
    try:
        # TODO: Implement Kaspi API integration
        logger.info(f"Syncing product {product_id} to Kaspi")

        # Simulate API call
        import time

        time.sleep(3)

        return True
    except Exception as e:
        logger.error(f"Failed to sync product {product_id} to Kaspi: {e}")
        return False


@celery_app.task
def process_payment_webhook(webhook_data: dict) -> bool:
    """Process payment webhook from TipTop Pay."""
    try:
        # TODO: Implement payment processing
        logger.info(f"Processing payment webhook: {webhook_data.get('invoice_id')}")

        # Simulate payment processing
        import time

        time.sleep(1)

        return True
    except Exception as e:
        logger.error(f"Failed to process payment webhook: {e}")
        return False


@celery_app.task
def generate_daily_report() -> dict:
    """Generate daily sales report."""
    try:
        logger.info("Generating daily report")

        # TODO: Implement report generation
        # Simulate report generation
        import time

        time.sleep(5)

        return {
            "date": "2024-01-01",
            "total_sales": 0,
            "total_orders": 0,
            "new_customers": 0,
        }
    except Exception as e:
        logger.error(f"Failed to generate daily report: {e}")
        return {"error": str(e)}


@celery_app.task
def cleanup_expired_otps() -> int:
    """Clean up expired OTP codes."""
    try:
        # For Celery tasks, we need to handle async operations properly
        import asyncio
        from datetime import datetime

        from app.core.db import async_session_maker
        from app.models.user import OTPCode

        async def _cleanup():
            async with async_session_maker() as db:
                # Delete expired OTP codes (using async SQLAlchemy)
                from sqlalchemy import delete

                result = await db.execute(delete(OTPCode).where(OTPCode.expires_at < datetime.utcnow()))
                deleted_count = result.rowcount
                await db.commit()
                logger.info(f"Cleaned up {deleted_count} expired OTP codes")
                return deleted_count

        # Run the async operation
        return asyncio.run(_cleanup())

    except Exception as e:
        logger.error(f"Failed to cleanup expired OTPs: {e}")
        return 0


@celery_app.task
def cleanup_expired_sessions() -> int:
    """Clean up expired user sessions."""
    try:
        # For Celery tasks, we need to handle async operations properly
        import asyncio
        from datetime import datetime

        from app.core.db import async_session_maker
        from app.models.user import UserSession

        async def _cleanup():
            async with async_session_maker() as db:
                # Delete expired sessions (using async SQLAlchemy)
                from sqlalchemy import delete

                result = await db.execute(delete(UserSession).where(UserSession.expires_at < datetime.utcnow()))
                deleted_count = result.rowcount
                await db.commit()
                logger.info(f"Cleaned up {deleted_count} expired sessions")
                return deleted_count

        # Run the async operation
        return asyncio.run(_cleanup())

    except Exception as e:
        logger.error(f"Failed to cleanup expired sessions: {e}")
        return 0
