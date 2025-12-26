from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.crypto import decrypt_json
from app.models.system_integrations import SystemActiveProvider, SystemIntegration

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
    config: Dict[str, Any]
    version: int
    cached_at: float


class ProviderRegistry:
    _cache: dict[str, CachedProvider] = {}
    _redis_client: Any | None = None
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
        if cls._redis_client is None:
            try:
                cls._redis_client = aioredis.from_url(
                    settings.REDIS_URL,
                    encoding="utf-8",
                    decode_responses=True,
                )
            except Exception as exc:  # pragma: no cover - runtime guard
                log.warning("ProviderRegistry: redis init failed", exc_info=exc)
                return None
        return cls._redis_client

    @classmethod
    async def _ensure_listener(cls) -> None:
        if cls._listener_task or not _HAS_REDIS:
            return
        client = await cls._redis_client()
        if not client:
            return
        pubsub = client.pubsub()
        await pubsub.subscribe(_CHANNEL)

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
        if not client:
            return
        payload = json.dumps({"domain": domain, "version": version, "ts": time.time()})
        try:
            await client.publish(_CHANNEL, payload)
        except Exception as exc:  # pragma: no cover - diagnostics
            log.warning("ProviderRegistry publish failed", exc_info=exc)

    @classmethod
    async def _load_active(cls, db: Any, domain: str) -> Optional[CachedProvider]:
        domain_key = cls._normalize_domain(domain)
        stmt_active = select(SystemActiveProvider).where(SystemActiveProvider.domain == domain_key)

        if isinstance(db, AsyncSession):
            res_active = await db.execute(stmt_active)
            active = res_active.scalar_one_or_none()
        else:
            active = db.execute(stmt_active).scalar_one_or_none()

        if not active:
            return None

        stmt_int = (
            select(SystemIntegration)
            .where(
                SystemIntegration.domain == domain_key,
                SystemIntegration.provider == active.provider,
                SystemIntegration.is_enabled.is_(True),
            )
            .order_by(SystemIntegration.version.desc())
            .limit(1)
        )

        if isinstance(db, AsyncSession):
            res_int = await db.execute(stmt_int)
            integ = res_int.scalar_one_or_none()
        else:
            integ = db.execute(stmt_int).scalar_one_or_none()

        if not integ:
            return None

        config: Dict[str, Any] = {}
        try:
            if integ.config_encrypted:
                config = decrypt_json(integ.config_encrypted)
        except Exception:
            log.warning("ProviderRegistry: decrypt failed for %s/%s", domain_key, integ.provider)
            config = {}

        return CachedProvider(
            provider=integ.provider,
            config=config,
            version=int(active.version or integ.version or 1),
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
    async def get_provider_config(cls, db: Any, domain: str) -> tuple[str | None, Dict[str, Any]]:
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
