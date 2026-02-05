from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import uuid4

from app.core.config import settings
from app.core.logging import get_logger
from app.integrations.errors import ProviderNotConfiguredError
from app.integrations.ports.payments import PaymentGateway, PaymentIntent

log = get_logger(__name__)


class PlaceholderPaymentGateway(PaymentGateway):
    """Minimal non-noop placeholder adapter for payments."""

    def __init__(
        self,
        name: str | None = None,
        config: dict[str, Any] | None = None,
        version: int | None = None,
    ):
        self.name = (name or "placeholder").strip() or "placeholder"
        self.config = config or {}
        self.version = int(version or 0)

    @property
    def provider_name(self) -> str:
        return self.name

    @property
    def provider_version(self) -> int:
        return self.version

    def _ensure_allowed(self) -> None:
        if settings.is_production:
            raise ProviderNotConfiguredError("payment_provider_unavailable")

    async def healthcheck(self) -> dict[str, Any]:
        return {
            "status": "ok",
            "provider": self.name,
            "version": self.version,
            "mode": "placeholder",
        }

    async def create_payment_intent(
        self,
        amount: Decimal,
        currency: str,
        customer_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> PaymentIntent:
        self._ensure_allowed()
        log.warning("Using placeholder payment gateway (non-production)")
        intent_id = uuid4().hex
        return PaymentIntent(
            id=intent_id,
            status="placeholder",
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
        self._ensure_allowed()
        log.warning("Using placeholder payment gateway (non-production)")
        return {
            "status": "placeholder",
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


__all__ = ["PlaceholderPaymentGateway"]
