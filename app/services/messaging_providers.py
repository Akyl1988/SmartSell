from __future__ import annotations

import asyncio
from typing import Any

from app.core.logging import get_logger
from app.core.provider_registry import ProviderRegistry
from app.integrations.ports.messaging import MessagingProvider
from app.integrations.providers.noop.messaging import NoOpMessagingProvider

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

        log.warning("Unknown messaging provider '%s', using noop", name)
        return NoOpMessagingProvider(name=name, config=config, version=version)

    @classmethod
    async def resolve(cls, db: Any, *, domain: str = "messaging") -> MessagingProvider:
        domain_key = (domain or "messaging").strip().lower() or "messaging"

        try:
            entry = await ProviderRegistry.get_active_provider(db, domain_key)
        except Exception as exc:  # pragma: no cover - runtime guard
            log.warning("Messaging provider resolution failed; using fallback noop", exc_info=exc)
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
            log.warning("Messaging provider not configured for domain '%s'; using noop", domain_key)
            instance = NoOpMessagingProvider(name="noop", config={}, version=0)
            async with cls._lock:
                cls._cache[cache_key] = instance
            return instance

        try:
            instance = cls._build_provider(entry.provider, entry.config, int(entry.version or 0))
        except Exception as exc:  # pragma: no cover - runtime guard
            log.warning("Messaging provider build failed; using noop", exc_info=exc)
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
