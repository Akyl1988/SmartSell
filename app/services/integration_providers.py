from __future__ import annotations

from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.integration_provider import IntegrationProvider, IntegrationProviderEvent


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


class IntegrationProviderService:
    @staticmethod
    async def list_providers(db: Any, domain: str | None = None) -> list[IntegrationProvider]:
        stmt = select(IntegrationProvider)
        if domain:
            stmt = stmt.where(IntegrationProvider.domain == _normalize_domain(domain))
        res = await _execute(db, stmt.order_by(IntegrationProvider.domain, IntegrationProvider.provider))
        return list(res.scalars().all())

    @staticmethod
    async def get_provider(db: Any, provider_id: int) -> IntegrationProvider | None:
        stmt = select(IntegrationProvider).where(IntegrationProvider.id == provider_id)
        res = await _execute(db, stmt)
        return res.scalar_one_or_none()

    @staticmethod
    async def get_by_pair(db: Any, domain: str, provider: str) -> IntegrationProvider | None:
        stmt = (
            select(IntegrationProvider)
            .where(
                IntegrationProvider.domain == _normalize_domain(domain),
                IntegrationProvider.provider == provider,
            )
            .limit(1)
        )
        res = await _execute(db, stmt)
        return res.scalar_one_or_none()

    @staticmethod
    async def get_active(db: Any, domain: str) -> IntegrationProvider | None:
        stmt = (
            select(IntegrationProvider)
            .where(
                IntegrationProvider.domain == _normalize_domain(domain),
                IntegrationProvider.is_active.is_(True),
                IntegrationProvider.is_enabled.is_(True),
            )
            .limit(1)
        )
        res = await _execute(db, stmt)
        return res.scalar_one_or_none()

    @staticmethod
    async def create_provider(
        db: Any,
        *,
        domain: str,
        provider: str,
        config: dict[str, Any] | None,
        capabilities: dict[str, Any] | None,
        is_enabled: bool = True,
        is_active: bool = False,
        actor_user_id: int | None = None,
    ) -> IntegrationProvider:
        item = IntegrationProvider(
            domain=_normalize_domain(domain),
            provider=provider,
            config_json=config or {},
            capabilities=capabilities,
            is_enabled=is_enabled,
            is_active=False,
            version=1,
            updated_by_user_id=actor_user_id,
        )
        db.add(item)
        await _commit(db)
        await _refresh(db, item)

        if is_active:
            item, _, _ = await IntegrationProviderService.set_active_provider(
                db,
                domain=item.domain,
                provider=item.provider,
                actor_user_id=actor_user_id,
            )
        return item

    @staticmethod
    async def update_provider(
        db: Any,
        provider_id: int,
        *,
        config: dict[str, Any] | None = None,
        capabilities: dict[str, Any] | None = None,
        is_enabled: bool | None = None,
        is_active: bool | None = None,
        actor_user_id: int | None = None,
    ) -> IntegrationProvider | None:
        item = await IntegrationProviderService.get_provider(db, provider_id)
        if not item:
            return None

        changed = False
        if config is not None:
            item.config_json = config
            changed = True
        if capabilities is not None:
            item.capabilities = capabilities
            changed = True
        if is_enabled is not None:
            item.is_enabled = is_enabled
            changed = True

        if changed:
            item.version = (item.version or 1) + 1
            item.updated_by_user_id = actor_user_id
            db.add(item)
            await _commit(db)
            await _refresh(db, item)

        # activation/deactivation handled after persisting config changes
        if is_active is True:
            item, _, _ = await IntegrationProviderService.set_active_provider(
                db,
                domain=item.domain,
                provider=item.provider,
                actor_user_id=actor_user_id,
            )
        elif is_active is False and item.is_active:
            item.is_active = False
            item.version = (item.version or 1) + 1
            item.updated_by_user_id = actor_user_id
            db.add(item)
            await _commit(db)
            await _refresh(db, item)

        return item

    @staticmethod
    async def delete_provider(db: Any, provider_id: int) -> tuple[bool, bool, str | None]:
        item = await IntegrationProviderService.get_provider(db, provider_id)
        if not item:
            return False, False, None
        domain = item.domain
        was_active = bool(item.is_active)
        db.delete(item)
        await _commit(db)
        return True, was_active, domain

    @staticmethod
    async def set_active_provider(
        db: Any,
        *,
        domain: str,
        provider: str,
        actor_user_id: int | None = None,
        meta: dict[str, Any] | None = None,
    ) -> tuple[IntegrationProvider, bool, IntegrationProviderEvent | None]:
        domain_key = _normalize_domain(domain)
        target = await IntegrationProviderService.get_by_pair(db, domain_key, provider)
        if not target or not target.is_enabled:
            raise LookupError("integration_provider_not_found_or_disabled")

        current = await IntegrationProviderService.get_active(db, domain_key)
        if current and current.provider == provider:
            return current, False, None

        # deactivate previously active provider for the domain
        await _execute(
            db,
            update(IntegrationProvider)
            .where(IntegrationProvider.domain == domain_key, IntegrationProvider.is_active.is_(True))
            .values(is_active=False),
        )

        target.is_active = True
        target.version = (target.version or 1) + 1
        target.updated_by_user_id = actor_user_id
        db.add(target)

        event = IntegrationProviderEvent(
            domain=domain_key,
            provider_from=current.provider if current else None,
            provider_to=provider,
            actor_user_id=actor_user_id,
            meta_json=meta or {},
        )
        db.add(event)

        await _commit(db)
        await _refresh(db, target)
        await _refresh(db, event)

        return target, True, event


__all__ = ["IntegrationProviderService"]
