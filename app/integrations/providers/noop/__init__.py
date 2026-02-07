from __future__ import annotations

from app.integrations.providers.noop.media import NoOpMediaProvider
from app.integrations.providers.noop.messaging import NoOpMessagingProvider
from app.integrations.providers.noop.otp import NoOpOtpProvider
from app.integrations.providers.noop.payments import NoOpPaymentGateway

__all__ = ["NoOpPaymentGateway", "NoOpOtpProvider", "NoOpMessagingProvider", "NoOpMediaProvider"]
