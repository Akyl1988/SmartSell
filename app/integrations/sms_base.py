# app/integrations/sms_base.py
from __future__ import annotations
import os
from typing import Protocol, Dict, Any

class SmsProvider(Protocol):
    def send_sms(self, recipient: str, text: str, *, sender: str | None = None) -> Dict[str, Any]: ...

def get_sms_client() -> SmsProvider:
    provider = (os.getenv("SMS_PROVIDER") or "mobizon").strip().lower()
    if provider == "mobizon":
        from app.integrations.mobizon import MobizonClient
        return MobizonClient()
    raise RuntimeError(f"Unsupported SMS_PROVIDER: {provider}")
