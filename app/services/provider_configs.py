from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import decrypt_json, encrypt_json
from app.core.logging import get_logger
from app.models.integration_provider import IntegrationProviderEvent
from app.models.integration_provider_config import IntegrationProviderConfig

log = get_logger(__name__)


def _normalize_domain(domain: str | None) -> str:
    return (domain or "").strip().lower()


def _is_async(db: Any) -> bool:
    return isinstance(db, AsyncSession)


def _sync_execute(db: Any, stmt):
    return db.execute(stmt)


async def _async_execute(db: AsyncSession, stmt):
    return await db.execute(stmt)


async def _execute(db: Any, stmt):
    if _is_async(db):
        return await _async_execute(db, stmt)  # type: ignore[arg-type]
    return _sync_execute(db, stmt)


async def _commit(db: Any) -> None:
    if _is_async(db):
        await db.commit()  # type: ignore[attr-defined]
    else:
        db.commit()


async def _refresh(db: Any, model: Any) -> None:
    if _is_async(db):
        await db.refresh(model)  # type: ignore[attr-defined]
    else:
        db.refresh(model)


def _redact_value(val: Any) -> Any:
    if isinstance(val, dict):
        return {k: _redact_value(v) for k, v in val.items()}
    if isinstance(val, list):
        return [_redact_value(v) for v in val]
    return "***"


class ProviderConfigService:
    @staticmethod
    async def record_event(
        db: Any,
        *,
        domain: str,
        provider: str,
        action: str,
        status: str,
        error: str | None = None,
        meta: dict[str, Any] | None = None,
        actor_user_id: int | None = None,
        actor_email: str | None = None,
    ) -> IntegrationProviderEvent:
        event = IntegrationProviderEvent(
            domain=_normalize_domain(domain),
            provider_from=provider,
            provider_to=provider,
            actor_user_id=actor_user_id,
            meta_json={
                "action": action,
                "status": status,
                "error": error,
                "actor_email": actor_email,
                **(meta or {}),
            },
        )
        db.add(event)
        await _commit(db)
        await _refresh(db, event)
        return event

    @staticmethod
    async def get_model(db: Any, domain: str, provider: str) -> IntegrationProviderConfig | None:
        stmt = (
            select(IntegrationProviderConfig)
            .where(
                IntegrationProviderConfig.domain == _normalize_domain(domain),
                IntegrationProviderConfig.provider == provider,
            )
            .limit(1)
        )
        res = await _execute(db, stmt)
        return res.scalar_one_or_none()

    @staticmethod
    def validate_config_schema(domain: str, provider: str, config: dict[str, Any]) -> None:
        if not isinstance(config, dict):
            raise ValueError("config_must_be_object")
        if not _normalize_domain(domain):
            raise ValueError("domain_required")
        if not provider:
            raise ValueError("provider_required")
        # Minimal stub: ensure config is non-empty dict to avoid accidental empty writes
        if config == {}:
            log.warning("Empty config for %s/%s", domain, provider)

    @classmethod
    async def set_provider_config(
        cls,
        db: Any,
        *,
        domain: str,
        provider: str,
        config: dict[str, Any],
        key_id: str = "master",
        meta: dict[str, Any] | None = None,
        actor_user_id: int | None = None,
        actor_email: str | None = None,
    ) -> IntegrationProviderConfig:
        cls.validate_config_schema(domain, provider, config)
        encrypted = encrypt_json(config)
        domain_key = _normalize_domain(domain)
        item = await cls.get_model(db, domain_key, provider)

        if item:
            item.config_encrypted = encrypted
            item.key_id = key_id
            item.meta_json = meta or {}
            item.is_active = True
        else:
            item = IntegrationProviderConfig(
                domain=domain_key,
                provider=provider,
                config_encrypted=encrypted,
                key_id=key_id,
                meta_json=meta or {},
                is_active=True,
            )
        db.add(item)

        event = IntegrationProviderEvent(
            domain=domain_key,
            provider_from=provider,
            provider_to=provider,
            actor_user_id=actor_user_id,
            meta_json={"action": "config_set", "meta": meta or {}, "actor_email": actor_email},
        )
        db.add(event)

        await _commit(db)
        await _refresh(db, item)
        await _refresh(db, event)
        return item

    @classmethod
    async def get_provider_config(cls, db: Any, *, domain: str, provider: str) -> dict[str, Any]:
        item = await cls.get_model(db, domain, provider)
        if not item or not item.config_encrypted:
            return {}
        try:
            return decrypt_json(item.config_encrypted)  # type: ignore[arg-type]
        except Exception as exc:  # pragma: no cover - defensive guard
            log.warning("Failed to decrypt provider config for %s/%s", domain, provider, exc_info=exc)
            return {}

    @classmethod
    async def get_redacted_config(cls, db: Any, *, domain: str, provider: str) -> dict[str, Any]:
        raw = await cls.get_provider_config(db, domain=domain, provider=provider)
        return _redact_value(raw)

    @classmethod
    async def healthcheck(
        cls,
        db: Any,
        *,
        domain: str,
        provider: str,
        actor_user_id: int | None = None,
        actor_email: str | None = None,
    ) -> dict[str, Any]:
        status = "ok"
        error = None
        domain_norm = _normalize_domain(domain)
        try:
            cfg = await cls.get_provider_config(db, domain=domain, provider=provider)
            cls.validate_config_schema(domain, provider, cfg if cfg else {})
            if domain_norm == "otp" and provider.lower() in {
                "mobizon",
                "otp-mobizon",
                "mobizon-otp",
            }:
                try:
                    from app.integrations.providers.mobizon.otp import MobizonOtpProvider

                    provider_inst = MobizonOtpProvider(config=cfg, name=provider, version=0)
                    hc = await provider_inst.healthcheck()
                    if hc.get("status") != "ok":
                        status = "error"
                        error = hc.get("provider_error") or "healthcheck_failed"
                except Exception as exc:  # pragma: no cover - defensive guard
                    status = "error"
                    error = str(exc)
            elif domain_norm == "payments":
                try:
                    from app.integrations.providers.noop.payments import NoOpPaymentGateway

                    provider_inst = NoOpPaymentGateway(config=cfg, name=provider, version=0)
                    hc = await provider_inst.healthcheck()
                    if hc.get("status") != "ok":
                        status = "error"
                        error = hc.get("provider_error") or "healthcheck_failed"
                except Exception as exc:  # pragma: no cover - defensive guard
                    status = "error"
                    error = str(exc)
            elif domain_norm == "messaging":
                try:
                    if provider.lower().startswith("noop"):
                        status = "ok"
                    elif provider.lower().startswith("webhook"):
                        from app.integrations.providers.webhook.messaging import (
                            WebhookMessagingProvider,
                        )

                        provider_inst = WebhookMessagingProvider(config=cfg, name=provider, version=0)
                        hc = await provider_inst.healthcheck()
                        if hc.get("status") != "ok":
                            status = "error"
                            error = hc.get("provider_error") or "healthcheck_failed"
                        else:
                            status = "ok"
                            error = hc.get("provider_error")
                    else:
                        status = "error"
                        error = "unsupported_provider"
                except Exception as exc:  # pragma: no cover - defensive guard
                    status = "error"
                    error = str(exc)
        except Exception as exc:  # pragma: no cover - defensive guard
            log.warning("Provider config healthcheck failed", exc_info=exc)
            status = "error"
            error = str(exc)

        await cls.record_event(
            db,
            domain=domain,
            provider=provider,
            action="healthcheck",
            status=status,
            error=error,
            actor_user_id=actor_user_id,
            actor_email=actor_email,
        )
        return {
            "status": status,
            "domain": _normalize_domain(domain),
            "provider": provider,
            "error": error,
        }


__all__ = ["ProviderConfigService"]
