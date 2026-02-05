from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import uuid4

from fastapi import HTTPException, status

from app.core.config import settings
from app.core.logging import get_logger
from app.integrations.ports.payments import PaymentGateway, PaymentIntent


log = get_logger(__name__)


class NoOpPaymentGateway(PaymentGateway):
    """Minimal no-op gateway for testing/wiring."""

    def __init__(
        self,
        name: str | None = None,
        config: dict[str, Any] | None = None,
        version: int | None = None,
    ):
        self.name = (name or "noop").strip() or "noop"
        self.config = config or {}
        self.version = int(version or 0)

    @property
    def provider_name(self) -> str:
        return self.name

    @property
    def provider_version(self) -> int:
        return self.version

    async def healthcheck(self) -> dict[str, Any]:
        if settings.is_production:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "payment_provider_not_configured")
        log.warning("Using noop payment gateway (non-production)")
        return {
            "status": "ok",
            "provider": self.name,
            "version": self.version,
        }

    async def create_payment_intent(
        self,
        amount: Decimal,
        currency: str,
        customer_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> PaymentIntent:
        if settings.is_production:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "payment_provider_not_configured")
        log.warning("Using noop payment gateway (non-production)")
        intent_id = uuid4().hex
        return PaymentIntent(
            id=intent_id,
            status="created",
            amount=Decimal(str(amount)),
            currency=(currency or "").upper(),
            customer_id=customer_id,
            provider=self.name,
            provider_intent_id=intent_id,
            provider_version=self.version,
            metadata=metadata or {},
        )

    async def refund(
        self,
        transaction_id: str,
        amount: float | None = None,
        reason: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if settings.is_production:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "payment_provider_not_configured")
        log.warning("Using noop payment gateway (non-production)")
        return {
            "status": "noop",
            "provider": self.name,
            "version": self.version,
            "transaction_id": transaction_id,
            "refunded": True,
            "amount": amount,
            "reason": reason,
            "metadata": metadata or {},
            "config": self.config,
        }

    # Backward-compat alias
    async def charge(
        self,
        amount: Decimal,
        currency: str,
        customer_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return await self.create_payment_intent(
            amount=amount,
            currency=currency,
            customer_id=customer_id,
            metadata=metadata,
        )


__all__ = ["NoOpPaymentGateway"]
