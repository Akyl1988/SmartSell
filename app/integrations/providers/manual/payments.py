from __future__ import annotations

from decimal import Decimal
from typing import Any

from app.core.logging import get_logger
from app.integrations.errors import ProviderNotConfiguredError
from app.integrations.ports.payments import PaymentGateway, PaymentIntent

log = get_logger(__name__)


class ManualPaymentGateway(PaymentGateway):
    """Manual billing only: no online charge/refund operations."""

    def __init__(
        self,
        name: str | None = None,
        config: dict[str, Any] | None = None,
        version: int | None = None,
    ):
        self.name = (name or "manual").strip() or "manual"
        self.config = config or {}
        self.version = int(version or 0)

    @property
    def provider_name(self) -> str:
        return self.name

    @property
    def provider_version(self) -> int:
        return self.version

    async def healthcheck(self) -> dict[str, Any]:
        return {
            "status": "ok",
            "provider": self.name,
            "version": self.version,
            "mode": "manual",
        }

    async def create_payment_intent(
        self,
        amount: Decimal,
        currency: str,
        customer_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> PaymentIntent:
        log.info("Manual billing mode: payment intent not allowed")
        raise ProviderNotConfiguredError("payment_provider_unavailable")

    async def refund(
        self,
        transaction_id: str,
        amount: float | None = None,
        reason: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        log.info("Manual billing mode: refund not allowed")
        raise ProviderNotConfiguredError("payment_provider_unavailable")

    async def charge(
        self,
        amount: Decimal,
        currency: str,
        customer_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        log.info("Manual billing mode: charge not allowed")
        raise ProviderNotConfiguredError("payment_provider_unavailable")


__all__ = ["ManualPaymentGateway"]
