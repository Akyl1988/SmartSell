# app/integrations/mobizon.py
from __future__ import annotations
import os
import httpx
import logging
from typing import Dict, Any

log = logging.getLogger(__name__)

class MobizonClient:
    def __init__(self) -> None:
        self.base_url = (os.getenv("MOBIZON_BASE_URL") or "https://api.mobizon.kz/service").rstrip("/")
        self.api_key  = os.getenv("MOBIZON_API_KEY") or ""
        self.sender   = os.getenv("MOBIZON_FROM") or None
        if not self.api_key:
            raise RuntimeError("MOBIZON_API_KEY is not set")

        # готовим клиент (keep-alive)
        self._client = httpx.Client(
            base_url=self.base_url,
            headers={"Authorization": f"Token {self.api_key}"},
            timeout=15.0,
        )

    def send_sms(self, recipient: str, text: str, *, sender: str | None = None) -> Dict[str, Any]:
        """
        Отправка SMS. Возвращает dict с success/error, raw ответом провайдера.
        Документация Mobizon: /service/message/sendSms (формат form-encoded).
        """
        if not recipient or not text:
            raise ValueError("recipient and text are required")

        payload = {
            "recipient": recipient,
            "text": text,
        }
        frm = sender or self.sender
        if frm:
            payload["from"] = frm

        # Mobizon принимает form-data/URL-encoded; ответ JSON
        url = "/message/sendSms"
        try:
            resp = self._client.post(url, data=payload)
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPError as e:
            log.warning("Mobizon HTTP error: %s", e)
            raise RuntimeError(f"mobizon_http_error: {e}") from e
        except Exception as e:
            log.exception("Mobizon unexpected error")
            raise

        # Унифицируем ответ
        # У Mobizon обычно: {"code":0,"data":{"messageId":"..."},"message":""} при успехе
        success = False
        message_id = None
        error = None

        try:
            code = data.get("code")
            success = (code == 0)
            if success:
                message_id = (data.get("data") or {}).get("messageId")
            else:
                error = data.get("message") or str(data)
        except Exception:
            error = "invalid_response_format"

        return {
            "provider": "mobizon",
            "success": bool(success),
            "message_id": message_id,
            "error": error,
            "raw": data,
        }
