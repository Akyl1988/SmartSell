from __future__ import annotations

import asyncio
from typing import Any

from app.core.config import settings
from app.core.logging import get_logger
from app.core.provider_registry import ProviderRegistry
from app.integrations.errors import ProviderNotConfiguredError
from app.integrations.ports.messaging import MessagingProvider
from app.integrations.providers.noop.messaging import NoOpMessagingProvider
from app.integrations.providers.smtp.messaging import SmtpMessagingProvider
from app.integrations.providers.webhook.messaging import WebhookMessagingProvider
from app.services.provider_configs import ProviderConfigService

log = get_logger(__name__)


class MessagingProviderResolver:
    _cache: dict[tuple[str, int, str], MessagingProvider] = {}
    _lock = asyncio.Lock()

    @classmethod
    def reset_cache(cls) -> None:
        cls._cache.clear()

    @classmethod
    def _cache_key(cls, domain: str, version: int | None, provider: str | None) -> tuple[str, int, str]:
        return (domain, int(version or 0), (provider or "noop"))

    @classmethod
    def _build_provider(
        cls,
        provider_name: str | None,
        config: dict[str, Any],
        version: int,
    ) -> MessagingProvider:
        name = (provider_name or "noop").strip() or "noop"
        normalized = name.lower()

        if normalized.startswith("noop"):
            return NoOpMessagingProvider(name=name, config=config, version=version)

        if normalized in {"smtp", "email-smtp", "smtp-email"}:
            return SmtpMessagingProvider(name=name, config=config, version=version)

        if normalized.startswith("webhook"):
            return WebhookMessagingProvider(name=name, config=config, version=version)

        log.warning("Unknown messaging provider '%s', using noop", name)
        return NoOpMessagingProvider(name=name, config=config, version=version)

    @classmethod
    async def resolve(cls, db: Any, *, domain: str = "messaging") -> MessagingProvider:
        domain_key = (domain or "messaging").strip().lower() or "messaging"

        try:
            entry = await ProviderRegistry.get_active_provider(db, domain_key)
        except Exception as exc:  # pragma: no cover - runtime guard
            log.warning("Messaging provider resolution failed; using fallback noop", exc_info=exc)
            if settings.is_production:
                raise ProviderNotConfiguredError("messaging_provider_not_configured")
            fallback = NoOpMessagingProvider(name="noop", config={}, version=0)
            async with cls._lock:
                cls._cache[cls._cache_key(domain_key, 0, "noop")] = fallback
            return fallback

        provider_name = getattr(entry, "provider", "noop") if entry else "noop"
        cache_key = cls._cache_key(domain_key, entry.version if entry else 0, provider_name)
        cached = cls._cache.get(cache_key)
        if cached:
            return cached

        if not entry:
            if settings.is_production:
                raise ProviderNotConfiguredError("messaging_provider_not_configured")
            log.warning("Messaging provider not configured for domain '%s'; using noop", domain_key)
            instance = NoOpMessagingProvider(name="noop", config={}, version=0)
            async with cls._lock:
                cls._cache[cache_key] = instance
            return instance

        provider_config = entry.config or {}

        if not provider_name.lower().startswith("noop"):
            try:
                provider_config = await ProviderConfigService.get_provider_config(
                    db, domain=domain_key, provider=entry.provider
                )
                if not provider_config:
                    await ProviderConfigService.record_event(
                        db,
                        domain=domain_key,
                        provider=entry.provider,
                        action="config_missing",
                        status="error",
                        error="provider_config_missing",
                    )
                    provider_config = {}
            except Exception as exc:  # pragma: no cover - runtime guard
                log.warning("Messaging provider config fetch failed; using noop", exc_info=exc)
                provider_config = {}

        try:
            if settings.is_production and (provider_name or "noop").lower().startswith("noop"):
                raise ProviderNotConfiguredError("messaging_provider_not_configured")
            instance = cls._build_provider(entry.provider, provider_config, int(entry.version or 0))
        except Exception as exc:  # pragma: no cover - runtime guard
            log.warning("Messaging provider build failed; using noop", exc_info=exc)
            await ProviderConfigService.record_event(
                db,
                domain=domain_key,
                provider=entry.provider,
                action="provider_build_failed",
                status="error",
                error=str(exc),
            )
            if settings.is_production:
                raise ProviderNotConfiguredError("messaging_provider_not_configured")
            instance = NoOpMessagingProvider(name="noop", config={}, version=0)

        async with cls._lock:
            cached_now = cls._cache.get(cache_key)
            if cached_now:
                return cached_now
            cls._cache[cache_key] = instance
            for key in list(cls._cache.keys()):
                if key[0] == domain_key and key != cache_key:
                    cls._cache.pop(key, None)

        return instance


__all__ = ["MessagingProviderResolver"]
