from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.integration_provider import IntegrationProvider

try:  # optional redis
    import redis.asyncio as aioredis  # type: ignore

    _HAS_REDIS = True
except Exception:  # pragma: no cover - optional dependency
    aioredis = None  # type: ignore
    _HAS_REDIS = False

log = logging.getLogger(__name__)

_CHANNEL = getattr(settings, "SYSTEM_CONFIG_CHANNEL", "smartsell.config_changed")
_TTL = max(1, int(getattr(settings, "SYSTEM_INTEGRATIONS_CACHE_TTL", 30) or 30))


@dataclass
class CachedProvider:
    provider: str
    config: dict[str, Any]
    version: int
    cached_at: float


class ProviderRegistry:
    _cache: dict[str, CachedProvider] = {}
    _redis_conn: Any | None = None
    _listener_task: asyncio.Task | None = None

    @staticmethod
    def _normalize_domain(domain: str | None) -> str:
        return (domain or "").strip().lower()

    @classmethod
    def invalidate(cls, domain: str | None = None) -> None:
        domain_key = cls._normalize_domain(domain)
        if domain_key:
            cls._cache.pop(domain_key, None)
        else:
            cls._cache.clear()

    @classmethod
    async def _redis(cls):
        # Deprecated alias kept for backward compatibility
        return await cls._redis_client()

    @classmethod
    async def _redis_client(cls):
        if not _HAS_REDIS:
            return None
        client = cls._redis_conn

        if callable(client):  # legacy callable factory support
            try:
                client = client()
            except Exception as exc:  # pragma: no cover - diagnostics
                log.warning("ProviderRegistry: redis factory failed", exc_info=exc)
                client = None

        if client is None:
            try:
                client = aioredis.from_url(
                    settings.REDIS_URL,
                    encoding="utf-8",
                    decode_responses=True,
                )
            except Exception as exc:  # pragma: no cover - runtime guard
                log.warning("ProviderRegistry: redis init failed", exc_info=exc)
                cls._redis_conn = None
                return None

        cls._redis_conn = client
        return client

    @classmethod
    async def _ensure_listener(cls) -> None:
        if cls._listener_task or not _HAS_REDIS:
            return
        client = await cls._redis_client()
        if not client or not hasattr(client, "pubsub"):
            return
        try:
            pubsub = client.pubsub()
            await pubsub.subscribe(_CHANNEL)
        except Exception as exc:  # pragma: no cover - diagnostics
            log.warning("ProviderRegistry listener subscribe failed", exc_info=exc)
            return

        async def _listen():
            try:
                async for msg in pubsub.listen():
                    if msg.get("type") != "message":
                        continue
                    try:
                        data = json.loads(msg.get("data") or "{}")
                        domain = cls._normalize_domain(data.get("domain"))
                        cls.invalidate(domain or None)
                    except Exception:
                        cls.invalidate()
            except asyncio.CancelledError:  # pragma: no cover - shutdown
                raise
            except Exception as exc:  # pragma: no cover - diagnostics
                log.warning("ProviderRegistry listener stopped", exc_info=exc)

        cls._listener_task = asyncio.create_task(_listen())

    @classmethod
    async def publish_change(cls, domain: str, version: int | None = None) -> None:
        client = await cls._redis_client()
        if not client or not hasattr(client, "publish"):
            return
        payload = json.dumps({"domain": domain, "version": version, "ts": time.time()})
        try:
            await client.publish(_CHANNEL, payload)
        except Exception as exc:  # pragma: no cover - diagnostics
            log.warning("ProviderRegistry publish failed", exc_info=exc)

    @classmethod
    async def _load_active(cls, db: Any, domain: str) -> Optional[CachedProvider]:
        domain_key = cls._normalize_domain(domain)
        stmt = (
            select(IntegrationProvider)
            .where(
                IntegrationProvider.domain == domain_key,
                IntegrationProvider.is_active.is_(True),
                IntegrationProvider.is_enabled.is_(True),
            )
            .order_by(IntegrationProvider.version.desc())
            .limit(1)
        )

        if isinstance(db, AsyncSession):
            res = await db.execute(stmt)
            integ = res.scalar_one_or_none()
        else:
            integ = db.execute(stmt).scalar_one_or_none()

        if not integ:
            return None

        config: dict[str, Any] = integ.config_json or {}

        return CachedProvider(
            provider=integ.provider,
            config=config,
            version=int(integ.version or 1),
            cached_at=time.monotonic(),
        )

    @classmethod
    async def get_active_provider(cls, db: Any, domain: str) -> Optional[CachedProvider]:
        domain_key = cls._normalize_domain(domain)
        await cls._ensure_listener()
        entry = cls._cache.get(domain_key)
        if entry and (time.monotonic() - entry.cached_at) < _TTL:
            return entry

        entry = await cls._load_active(db, domain_key)
        if entry:
            cls._cache[domain_key] = entry
        return entry

    @classmethod
    async def get_provider_config(cls, db: Any, domain: str) -> tuple[str | None, dict[str, Any]]:
        entry = await cls.get_active_provider(db, domain)
        if not entry:
            return None, {}
        return entry.provider, entry.config

    @classmethod
    async def notify_change(cls, domain: str, version: int | None = None) -> None:
        domain_key = cls._normalize_domain(domain)
        cls.invalidate(domain_key)
        await cls.publish_change(domain_key, version)
        await cls._ensure_listener()


__all__ = ["ProviderRegistry", "CachedProvider"]
