from __future__ import annotations

from typing import Any

from app.integrations.ports.payments import PaymentGateway


class NoOpPaymentGateway(PaymentGateway):
    """Minimal no-op gateway for testing/wiring."""

    async def charge(
        self,
        amount: float,
        currency: str,
        customer_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {
            "status": "noop",
            "transaction_id": "noop",
            "amount": amount,
            "currency": currency,
            "customer_id": customer_id,
            "metadata": metadata or {},
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
            "transaction_id": transaction_id,
            "refunded": True,
            "amount": amount,
            "reason": reason,
            "metadata": metadata or {},
        }


__all__ = ["NoOpPaymentGateway"]
