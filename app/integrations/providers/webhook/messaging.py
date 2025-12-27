from __future__ import annotations

import asyncio
from typing import Any

import httpx

from app.core.logging import get_logger
from app.integrations.ports.messaging import MessagingProvider

log = get_logger(__name__)


def _redact(val: Any) -> Any:
    return "***" if val else None


class WebhookMessagingProvider(MessagingProvider):
    def __init__(
        self,
        *,
        config: dict[str, Any] | None = None,
        name: str | None = None,
        version: int | None = None,
    ) -> None:
        cfg = config or {}
        self.name = (name or "webhook").strip() or "webhook"
        self.version = int(version or 0)
        self.url = (cfg.get("url") or "").strip()
        if not self.url:
            raise ValueError("webhook config missing url")
        self.api_key = cfg.get("api_key")
        self.timeout_seconds = float(cfg.get("timeout_s") or 5.0)
        self.retries = int(cfg.get("retries") or 2)

    def _headers(self) -> dict[str, str]:
        headers = {"User-Agent": "smartsell-webhook-messaging"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    async def _request(
        self,
        method: str,
        url: str,
        *,
        json: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        timeout = httpx.Timeout(self.timeout_seconds)
        attempt = 0
        last_exc: Exception | None = None
        hdrs = headers or self._headers()
        while attempt <= self.retries:
            try:
                async with httpx.AsyncClient(timeout=timeout) as client:
                    resp = await client.request(method, url, json=json, headers=hdrs)
                return resp
            except httpx.HTTPError as exc:  # network/timeout
                last_exc = exc
                attempt += 1
                if attempt > self.retries:
                    break
                await asyncio.sleep(0.05 * attempt)
        assert last_exc is not None  # defensive
        raise last_exc

    def _payload(self, status: str, extra: dict[str, Any] | None = None) -> dict[str, Any]:
        data: dict[str, Any] = {"status": status, "provider": self.name, "version": self.version}
        if extra:
            data.update(extra)
        return data

    def _log_warning(self, message: str, *, error: str | None = None) -> None:
        log.warning(
            message,
            extra={
                "provider": self.name,
                "version": self.version,
                "url": self.url,
                "api_key": _redact(self.api_key),
                "error": error,
            },
        )

    async def send_message(
        self,
        to: str,
        text: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload = {"to": to, "text": text, "metadata": metadata or {}}
        try:
            resp = await self._request("post", self.url, json=payload)
        except Exception as exc:  # pragma: no cover - network guard
            self._log_warning("Webhook messaging send failed", error=str(exc))
            return self._payload("error", {"provider_error": str(exc)})

        status_code = resp.status_code
        resp_json = resp.json() if resp.content else {}

        if status_code >= 500:
            return self._payload(
                "error",
                {
                    "provider_status": status_code,
                    "provider_error": resp_json.get("error") or "http_error",
                },
            )
        if status_code >= 400:
            return self._payload(
                "error",
                {
                    "provider_status": status_code,
                    "provider_error": resp_json.get("error") or "bad_request",
                },
            )
        return self._payload("ok", {"provider_status": status_code, "provider_response": resp_json})

    async def healthcheck(self) -> dict[str, Any]:
        headers = self._headers()
        methods = (
            ("head", None),
            ("get", None),
            ("post", {"ping": True}),
        )
        last_error: str | None = None
        for method, body in methods:
            try:
                resp = await self._request(method, self.url, json=body, headers=headers)
                if resp.status_code < 400:
                    return self._payload("ok", {"provider_status": resp.status_code})
                last_error = f"status_{resp.status_code}"
                if resp.status_code in {405, 404}:
                    continue
            except Exception as exc:  # pragma: no cover - network guard
                last_error = str(exc)
        self._log_warning("Webhook messaging healthcheck failed", error=last_error)
        return self._payload("error", {"provider_error": last_error or "healthcheck_failed"})


__all__ = ["WebhookMessagingProvider"]
