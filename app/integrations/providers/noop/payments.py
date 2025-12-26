from __future__ import annotations

from typing import Any

from app.integrations.ports.payments import PaymentGateway


class NoOpPaymentGateway(PaymentGateway):
    """Minimal no-op gateway for testing/wiring."""

    def __init__(self, name: str | None = None, config: dict[str, Any] | None = None, version: int | None = None):
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
        return {
            "status": "ok",
            "provider": self.name,
            "version": self.version,
        }

    async def create_payment_intent(
        self,
        amount: float,
        currency: str,
        customer_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {
            "status": "noop",
            "provider": self.name,
            "version": self.version,
            "payment_intent_id": "noop",
            "amount": amount,
            "currency": currency,
            "customer_id": customer_id,
            "metadata": metadata or {},
            "config": self.config,
        }

    async def refund(
        self,
        transaction_id: str,
        amount: float | None = None,
        reason: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
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
        amount: float,
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
