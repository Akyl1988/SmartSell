"""
Idempotency utilities for preventing duplicate operations.
"""

import hashlib
import json
from datetime import datetime, timedelta
from typing import Any, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger

logger = get_logger(__name__)


class IdempotencyRecord:
    """Idempotency record model (can be stored in database or cache)"""

    def __init__(self, key: str, result: Any, expires_at: datetime, created_at: datetime = None):
        self.key = key
        self.result = result
        self.expires_at = expires_at
        self.created_at = created_at or datetime.utcnow()

    def is_expired(self) -> bool:
        """Check if record is expired"""
        return datetime.utcnow() > self.expires_at

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary"""
        return {
            "key": self.key,
            "result": self.result,
            "expires_at": self.expires_at.isoformat(),
            "created_at": self.created_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "IdempotencyRecord":
        """Create from dictionary"""
        return cls(
            key=data["key"],
            result=data["result"],
            expires_at=datetime.fromisoformat(data["expires_at"]),
            created_at=datetime.fromisoformat(data["created_at"]),
        )


class IdempotencyManager:
    """Manager for idempotency operations"""

    def __init__(self):
        self.storage = {}  # In-memory storage for simplicity
        # In production, use Redis or database table

    def generate_key(
        self,
        operation: str,
        user_id: Optional[int] = None,
        company_id: Optional[int] = None,
        data: Optional[dict[str, Any]] = None,
        custom_key: Optional[str] = None,
    ) -> str:
        """Generate idempotency key"""

        if custom_key:
            return custom_key

        # Create key components
        components = [operation]

        if user_id:
            components.append(f"user:{user_id}")

        if company_id:
            components.append(f"company:{company_id}")

        if data:
            # Sort data for consistent hashing
            data_str = json.dumps(data, sort_keys=True)
            data_hash = hashlib.md5(data_str.encode()).hexdigest()
            components.append(f"data:{data_hash}")

        key = ":".join(components)

        # Hash the final key to ensure consistent length
        return hashlib.sha256(key.encode()).hexdigest()

    async def check_idempotency(self, key: str, ttl_hours: int = 24) -> Optional[Any]:
        """Check if operation was already performed"""

        try:
            record = self.storage.get(key)

            if record:
                if record.is_expired():
                    # Remove expired record
                    del self.storage[key]
                    logger.debug(f"Removed expired idempotency record: {key}")
                    return None
                else:
                    logger.info(f"Found idempotency record: {key}")
                    return record.result

            return None

        except Exception as e:
            logger.error(f"Idempotency check error: {e}")
            return None

    async def store_result(self, key: str, result: Any, ttl_hours: int = 24) -> bool:
        """Store operation result"""

        try:
            expires_at = datetime.utcnow() + timedelta(hours=ttl_hours)
            record = IdempotencyRecord(key, result, expires_at)

            self.storage[key] = record
            logger.debug(f"Stored idempotency record: {key}")
            return True

        except Exception as e:
            logger.error(f"Idempotency store error: {e}")
            return False

    async def cleanup_expired(self) -> int:
        """Clean up expired records"""

        try:
            expired_keys = []

            for key, record in self.storage.items():
                if record.is_expired():
                    expired_keys.append(key)

            for key in expired_keys:
                del self.storage[key]

            logger.info(f"Cleaned up {len(expired_keys)} expired idempotency records")
            return len(expired_keys)

        except Exception as e:
            logger.error(f"Idempotency cleanup error: {e}")
            return 0

    async def remove_record(self, key: str) -> bool:
        """Remove specific record"""

        try:
            if key in self.storage:
                del self.storage[key]
                logger.debug(f"Removed idempotency record: {key}")
                return True
            return False

        except Exception as e:
            logger.error(f"Idempotency remove error: {e}")
            return False


# Global idempotency manager
idempotency_manager = IdempotencyManager()


async def ensure_idempotency(
    db: AsyncSession, operation: str, identifier: str, ttl_hours: int = 24
) -> bool:
    """Check if operation was already performed (database-based)"""

    try:
        # For simplicity, we'll use a simple check in webhook context
        # In production, use dedicated idempotency table

        key = f"{operation}:{identifier}"

        # Check in-memory storage
        existing = await idempotency_manager.check_idempotency(key, ttl_hours)

        if existing is not None:
            return True  # Already processed

        # Store as processed
        await idempotency_manager.store_result(key, True, ttl_hours)
        return False  # Not processed yet

    except Exception as e:
        logger.error(f"Idempotency check error for {operation}:{identifier}: {e}")
        return False


class IdempotentOperation:
    """Decorator class for idempotent operations"""

    def __init__(self, operation: str, ttl_hours: int = 24, key_func: Optional[callable] = None):
        self.operation = operation
        self.ttl_hours = ttl_hours
        self.key_func = key_func

    def __call__(self, func):
        async def wrapper(*args, **kwargs):
            # Generate idempotency key
            if self.key_func:
                key = self.key_func(*args, **kwargs)
            else:
                key = idempotency_manager.generate_key(operation=self.operation, data=kwargs)

            # Check if already processed
            existing_result = await idempotency_manager.check_idempotency(key, self.ttl_hours)

            if existing_result is not None:
                logger.info(f"Returning cached result for {key}")
                return existing_result

            # Execute operation
            result = await func(*args, **kwargs)

            # Store result
            await idempotency_manager.store_result(key, result, self.ttl_hours)

            return result

        return wrapper


def payment_idempotency_key(order_id: int, amount: float, **kwargs) -> str:
    """Generate idempotency key for payment operations"""
    return f"payment:order:{order_id}:amount:{amount}"


def webhook_idempotency_key(provider: str, event_id: str, **kwargs) -> str:
    """Generate idempotency key for webhook operations"""
    return f"webhook:{provider}:event:{event_id}"


def import_idempotency_key(company_id: int, filename: str, **kwargs) -> str:
    """Generate idempotency key for import operations"""
    return f"import:company:{company_id}:file:{filename}"


async def with_idempotency(
    operation: str,
    func: callable,
    args: tuple = (),
    kwargs: dict = None,
    ttl_hours: int = 24,
    custom_key: str = None,
) -> Any:
    """Execute function with idempotency protection"""

    kwargs = kwargs or {}

    # Generate key
    if custom_key:
        key = custom_key
    else:
        key = idempotency_manager.generate_key(operation=operation, data=kwargs)

    # Check existing result
    existing_result = await idempotency_manager.check_idempotency(key, ttl_hours)

    if existing_result is not None:
        logger.info(f"Returning cached result for {operation}")
        return existing_result

    # Execute function
    if len(args) > 0:
        result = await func(*args, **kwargs)
    else:
        result = await func(**kwargs)

    # Store result
    await idempotency_manager.store_result(key, result, ttl_hours)

    return result


# Convenience functions for common operations


async def ensure_payment_idempotency(
    order_id: int, amount: float, provider_invoice_id: str
) -> bool:
    """Ensure payment idempotency"""

    key = f"payment:{provider_invoice_id}"
    existing = await idempotency_manager.check_idempotency(key)

    if existing:
        return True  # Already processed

    await idempotency_manager.store_result(key, True)
    return False  # Not processed yet


async def ensure_webhook_idempotency(provider: str, event_id: str) -> bool:
    """Ensure webhook idempotency"""

    key = webhook_idempotency_key(provider, event_id)
    existing = await idempotency_manager.check_idempotency(key)

    if existing:
        return True  # Already processed

    await idempotency_manager.store_result(key, True)
    return False  # Not processed yet


async def cleanup_idempotency_records() -> int:
    """Clean up expired idempotency records"""
    return await idempotency_manager.cleanup_expired()
