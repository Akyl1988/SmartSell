from __future__ import annotations

import asyncio
import os
from functools import lru_cache
from typing import Any

from fastapi import HTTPException, status

from app.core.config import settings
from app.core.logging import audit_logger, get_logger
from app.core.provider_registry import ProviderRegistry
from app.core.rbac import is_platform_admin, is_store_admin
from app.integrations.errors import ProviderNotConfiguredError
from app.integrations.ports.otp import OtpProvider
from app.integrations.providers.noop.otp import NoOpOtpProvider
from app.services.provider_configs import ProviderConfigService

try:  # optional, stub
    from app.integrations.providers.mobizon.otp import MobizonOtpProvider  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    MobizonOtpProvider = None  # type: ignore

log = get_logger(__name__)


class OtpProviderResolver:
    _cache: dict[tuple[str, int, str], OtpProvider] = {}
    _lock = asyncio.Lock()

    @classmethod
    def reset_cache(cls) -> None:
        cls._cache.clear()

    @classmethod
    def _cache_key(cls, domain: str, version: int | None, provider: str | None) -> tuple[str, int, str]:
        return (domain, int(version or 0), (provider or "noop"))

    @classmethod
    def _build_provider(cls, provider_name: str | None, config: dict[str, Any], version: int) -> OtpProvider:
        name = (provider_name or "noop").strip() or "noop"
        normalized = name.lower()

        if normalized.startswith("noop"):
            return NoOpOtpProvider(name=name, config=config, version=version)

        if normalized in {"mobizon", "otp-mobizon", "mobizon-otp"} and MobizonOtpProvider:
            return MobizonOtpProvider(config=config, name=name, version=version)

        log.warning("Unknown otp provider '%s', using noop", name)
        return NoOpOtpProvider(name=name, config=config, version=version)

    @classmethod
    async def resolve(cls, db: Any, *, domain: str = "otp") -> OtpProvider:
        domain_key = (domain or "otp").strip().lower() or "otp"

        try:
            entry = await ProviderRegistry.get_active_provider(db, domain_key)
        except Exception as exc:  # pragma: no cover - runtime guard
            log.warning("OTP provider resolution failed; using fallback noop", exc_info=exc)
            if settings.is_production:
                raise ProviderNotConfiguredError("otp_provider_not_configured")
            fallback = NoOpOtpProvider(name="noop", config={}, version=0)
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
                raise ProviderNotConfiguredError("otp_provider_not_configured")
            log.warning("OTP provider not configured for domain '%s'; using noop", domain_key)
            instance = NoOpOtpProvider(name="noop", config={}, version=0)
            async with cls._lock:
                cls._cache[cache_key] = instance
            return instance

        provider_config = entry.config or {}

        if entry.provider.lower() in {"mobizon", "otp-mobizon", "mobizon-otp"}:
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
                    if settings.is_production:
                        raise ProviderNotConfiguredError("otp_provider_not_configured")
                    log.warning("OTP provider config missing; using noop")
                    instance = NoOpOtpProvider(name="noop", config={}, version=0)
                    async with cls._lock:
                        cached_now = cls._cache.get(cache_key)
                        if cached_now:
                            return cached_now
                        cls._cache[cache_key] = instance
                        for key in list(cls._cache.keys()):
                            if key[0] == domain_key and key != cache_key:
                                cls._cache.pop(key, None)
                    return instance
            except Exception as exc:  # pragma: no cover - runtime guard
                log.warning("OTP provider config fetch failed; using noop", exc_info=exc)
                provider_config = {}

        try:
            if settings.is_production and (provider_name or "noop").lower().startswith("noop"):
                raise ProviderNotConfiguredError("otp_provider_not_configured")
            instance = cls._build_provider(entry.provider, provider_config, int(entry.version or 0))
        except Exception as exc:  # pragma: no cover - runtime guard
            log.warning("OTP provider build failed; using noop", exc_info=exc)
            await ProviderConfigService.record_event(
                db,
                domain=domain_key,
                provider=entry.provider,
                action="provider_build_failed",
                status="error",
                error=str(exc),
            )
            if settings.is_production:
                raise ProviderNotConfiguredError("otp_provider_not_configured")
            instance = NoOpOtpProvider(name="noop", config={}, version=0)

        async with cls._lock:
            cached_now = cls._cache.get(cache_key)
            if cached_now:
                return cached_now
            cls._cache[cache_key] = instance
            # drop stale entries for this domain to enforce hot-switch on next call
            for key in list(cls._cache.keys()):
                if key[0] == domain_key and key != cache_key:
                    cls._cache.pop(key, None)

        return instance


@lru_cache
def is_otp_active() -> bool:
    env_enabled = os.getenv("OTP_ENABLED") or os.getenv("OTP_ACTIVE")
    if env_enabled is not None:
        enabled = str(env_enabled).strip().lower() in {"1", "true", "yes", "on"}
    else:
        enabled = bool(getattr(settings, "OTP_ENABLED", True))

    env_provider = os.getenv("OTP_PROVIDER") or os.getenv("OTP_PROVIDER_NAME")
    if env_provider is not None:
        provider = str(env_provider).strip().lower()
    else:
        provider = str(getattr(settings, "OTP_PROVIDER", "noop") or "noop").strip().lower()

    if not enabled:
        return False
    if not provider or provider in {"noop", "none", "disabled", "off"}:
        return False
    return True


def require_otp_provider_or_admin_bypass(
    current_user: Any | None,
    *,
    action: str = "bootstrap",
    company_id: int | None = None,
    owner_id: int | None = None,
) -> None:
    if is_otp_active():
        return

    user = current_user
    if user is not None:
        is_admin = bool(is_platform_admin(user) or is_store_admin(user))
        is_owner = bool(owner_id and getattr(user, "id", None) == owner_id)
        if is_admin or is_owner:
            audit_logger.log_system_event(
                event="bootstrap_otp_bypass_used",
                message="OTP provider not configured; admin bootstrap bypass used",
                meta={
                    "actor_id": getattr(user, "id", None),
                    "company_id": company_id or getattr(user, "company_id", None),
                    "action": action,
                },
            )
            return

    raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="otp_provider_not_configured")


__all__ = ["OtpProviderResolver", "is_otp_active", "require_otp_provider_or_admin_bypass"]
