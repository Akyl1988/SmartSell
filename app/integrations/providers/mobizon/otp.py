from __future__ import annotations

import asyncio
import hashlib
from typing import Any

import httpx

from app.core.config import settings
from app.core.logging import get_logger
from app.integrations.errors import ProviderNotConfiguredError
from app.integrations.ports.otp import OtpProvider
from app.utils.pii import mask_phone

log = get_logger(__name__)


class MobizonOtpProvider(OtpProvider):
    def __init__(
        self,
        *,
        config: dict[str, Any] | None = None,
        name: str | None = None,
        version: int | None = None,
    ):
        cfg = config or {}
        self.name = (name or "mobizon").strip() or "mobizon"
        self.version = int(version or 0)
        self.api_key = (cfg.get("api_key") or cfg.get("api_token") or settings.MOBIZON_API_KEY or "").strip()
        self.sender = (cfg.get("sender") or cfg.get("alphasender") or settings.MOBIZON_SENDER or "").strip()
        self.base_url = (
            cfg.get("base_url") or settings.MOBIZON_BASE_URL or settings.MOBIZON_API_URL or "https://api.mobizon.kz"
        ).rstrip("/")
        self.timeout_seconds = float(cfg.get("timeout_s") or settings.MOBIZON_TIMEOUT_SEC or 5.0)
        self._max_retries = int(cfg.get("retries") or 1)
        if settings.is_production and not self.api_key:
            raise ProviderNotConfiguredError("otp_provider_not_configured")

    def _headers(self, idempotency_key: str | None = None) -> dict[str, str]:
        headers: dict[str, str] = {"User-Agent": "smartsell-mobizon-client"}
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        return headers

    def _auth_params(self) -> dict[str, str]:
        return {"apiKey": self.api_key}

    def _idempotency_from_payload(self, phone: str, code: str, ttl: int) -> str:
        raw = f"{phone}:{code}:{ttl}:{self.name}:{self.version}".encode()
        return hashlib.sha256(raw).hexdigest()

    async def _request(
        self,
        method: str,
        path: str,
        *,
        data: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        url = f"{self.base_url}{path}"
        timeout = httpx.Timeout(self.timeout_seconds)
        attempt = 0
        last_exc: Exception | None = None
        params_all = {**(params or {}), **self._auth_params()}
        while attempt <= self._max_retries:
            try:
                async with httpx.AsyncClient(timeout=timeout) as client:
                    resp = await client.request(method, url, data=data, params=params_all, headers=headers)
                return resp
            except (httpx.TimeoutException, httpx.RequestError) as exc:  # network/timeout
                last_exc = exc
                attempt += 1
                if attempt > self._max_retries:
                    raise
                await asyncio.sleep(0.1 * attempt)
        assert last_exc  # pragma: no cover - defensive
        raise last_exc

    def _success_payload(self, status: str, extra: dict[str, Any] | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "status": status,
            "provider": self.name,
            "version": self.version,
        }
        if extra:
            payload.update(extra)
        return payload

    async def send_otp(
        self,
        phone: str,
        code: str,
        ttl_seconds: int,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if settings.is_production and not self.api_key:
            raise ProviderNotConfiguredError("otp_provider_not_configured")
        # Do not log secrets/OTP code
        text = (metadata or {}).get("text") or code
        payload = {
            "recipient": phone,
            "text": text,
            "ttl": ttl_seconds,
        }
        if self.sender:
            payload["from"] = self.sender

        idem = self._idempotency_from_payload(phone, code, ttl_seconds)

        try:
            resp = await self._request(
                "post",
                "/service/message/sendSmsMessage",
                data=payload,
                headers=self._headers(idem),
            )
            if resp.status_code in {401, 403}:
                raise ProviderNotConfiguredError("otp_provider_auth_failed")
            if resp.status_code >= 400:
                return self._success_payload(
                    "error",
                    {
                        "provider_status": resp.status_code,
                        "success": None,
                    },
                )
            data = resp.json() if resp.content else {}
            if str(data.get("code", "0")) not in {"0", "200", "OK"} and data.get("error"):
                return self._success_payload(
                    "error",
                    {
                        "provider_status": resp.status_code,
                        "success": None,
                    },
                )
            message_id = (data.get("data") or {}).get("messageId") or data.get("messageId")
            return self._success_payload(
                "ok",
                {
                    "provider_status": resp.status_code,
                    "provider_message_id": message_id,
                    "success": True,
                },
            )
        except ProviderNotConfiguredError:
            raise
        except Exception as exc:  # pragma: no cover - defensive runtime
            log.warning("mobizon_send_failed", extra={"phone": mask_phone(phone), "error": str(exc)})
            raise ProviderNotConfiguredError("otp_provider_unavailable") from exc

    async def verify_otp(
        self,
        phone: str,
        code: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self._success_payload("unsupported", {"verified": False})

    async def healthcheck(self) -> dict[str, Any]:
        try:
            resp = await self._request("get", "/service/ping")
            if resp.status_code >= 400:
                return {"status": "error", "provider_status": resp.status_code}
            return {"status": "ok", "provider_status": resp.status_code}
        except Exception as exc:  # pragma: no cover - defensive runtime
            log.warning("Mobizon healthcheck failed", exc_info=exc)
            return {"status": "error", "provider_error": "healthcheck_failed"}


__all__ = ["MobizonOtpProvider"]
