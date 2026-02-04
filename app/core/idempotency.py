from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta
from typing import Optional

from fastapi import HTTPException, Request, Response, status
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.idempotency_key import IdempotencyKey

log = logging.getLogger(__name__)


class IdempotencyEnforcer:
    """Idempotency helper backed by PostgreSQL."""

    def __init__(
        self,
        redis=None,
        prefix: str = "idemp",
        default_ttl: int = 900,
        env: str = "dev",
    ) -> None:
        self.redis = redis
        self.prefix = prefix
        self.default_ttl = max(1, int(default_ttl or 1))
        self.env = env or "dev"
        self._mem: dict[str, tuple[int, float]] = {}

    async def reserve(
        self,
        db: AsyncSession,
        company_id: int,
        key: str,
        ttl_seconds: int,
    ) -> tuple[bool, Optional[int]]:
        ttl = max(1, int(ttl_seconds))
        now = datetime.utcnow()
        expires_at = now + timedelta(seconds=ttl)

        inserted = await self._insert_processing(db, company_id, key, expires_at)
        if inserted:
            await db.commit()
            return True, None

        existing = await self._get_existing(db, company_id, key)
        if existing and existing.expires_at and existing.expires_at <= now:
            await db.execute(
                delete(IdempotencyKey).where(
                    IdempotencyKey.company_id == company_id,
                    IdempotencyKey.key == key,
                )
            )
            await db.commit()
            inserted = await self._insert_processing(db, company_id, key, expires_at)
            if inserted:
                await db.commit()
                return True, None
            existing = await self._get_existing(db, company_id, key)

        if not existing:
            return True, None

        status_code = existing.status_code
        return False, int(status_code) if status_code is not None else None

    async def set_result(self, *args, **kwargs) -> None:
        if args and isinstance(args[0], AsyncSession):
            db = args[0]
            company_id = kwargs.get("company_id")
            key = kwargs.get("key")
            status_code = kwargs.get("status_code")
            ttl_seconds = kwargs.get("ttl_seconds")
            if company_id is None or key is None or status_code is None:
                return
            ttl = max(1, int(ttl_seconds or self.default_ttl))
            now = datetime.utcnow()
            expires_at = now + timedelta(seconds=ttl)
            stmt = (
                pg_insert(IdempotencyKey)
                .values(
                    company_id=int(company_id),
                    key=str(key),
                    status_code=int(status_code),
                    expires_at=expires_at,
                )
                .on_conflict_do_update(
                    index_elements=["company_id", "key"],
                    set_={
                        "status_code": int(status_code),
                        "expires_at": expires_at,
                        "updated_at": now,
                    },
                )
            )
            await db.execute(stmt)
            await db.commit()
            return

        key = args[0] if args else kwargs.get("key")
        status_code = args[1] if len(args) > 1 else kwargs.get("status_code")
        ttl_seconds = kwargs.get("ttl_seconds")
        if key is None or status_code is None:
            return
        await self._set_result_mem(str(key), int(status_code), ttl_seconds=ttl_seconds)

    async def _insert_processing(
        self,
        db: AsyncSession,
        company_id: int,
        key: str,
        expires_at: datetime,
    ) -> bool:
        stmt = (
            pg_insert(IdempotencyKey)
            .values(
                company_id=company_id,
                key=key,
                status_code=None,
                expires_at=expires_at,
            )
            .on_conflict_do_nothing(index_elements=["company_id", "key"])
        )
        res = await db.execute(stmt)
        return bool(res.rowcount and res.rowcount > 0)

    async def _get_existing(self, db: AsyncSession, company_id: int, key: str) -> IdempotencyKey | None:
        res = await db.execute(
            select(IdempotencyKey).where(IdempotencyKey.company_id == company_id, IdempotencyKey.key == key)
        )
        return res.scalar_one_or_none()

    async def _reserve_mem(self, key: str, ttl_seconds: int) -> tuple[bool, Optional[int]]:
        ttl = max(1, int(ttl_seconds))
        now = time.time()
        rec = self._mem.get(key)
        if rec:
            status_code, exp = rec
            if now < exp:
                return False, status_code
            self._mem.pop(key, None)
        self._mem[key] = (102, now + ttl)
        return True, None

    async def _set_result_mem(self, key: str, status_code: int, ttl_seconds: Optional[int] = None) -> None:
        ttl = max(1, int(ttl_seconds or self.default_ttl))
        self._mem[key] = (int(status_code), time.time() + ttl)

    def dependency(self, allow_replay: bool = False):
        async def dep(request: Request, response: Response):
            method = request.method.upper()
            if method not in ("POST", "PUT", "PATCH"):
                return True

            key = request.headers.get("Idempotency-Key")
            if not key:
                return True

            ttl_header = request.headers.get("Idempotency-TTL")
            try:
                ttl_seconds = int(ttl_header) if ttl_header else self.default_ttl
            except Exception:
                ttl_seconds = self.default_ttl

            allowed, processed_status = await self._reserve_mem(key, ttl_seconds)
            if not allowed:
                if processed_status is not None and allow_replay:
                    request.state.idempotency_key = key
                    request.state.idempotency_ttl = ttl_seconds
                    response.headers["Idempotency-Key"] = key
                    response.status_code = processed_status
                    return True
                detail = "Request already processed" if processed_status is not None else "Request is being processed"
                raise HTTPException(status.HTTP_409_CONFLICT, detail)

            request.state.idempotency_key = key
            request.state.idempotency_ttl = ttl_seconds
            response.headers["Idempotency-Key"] = key
            return True

        return dep


def ensure_idempotency_dep(enforcer: IdempotencyEnforcer | None = None, *args, **kwargs):
    if enforcer is not None:
        return enforcer.dependency(*args, **kwargs)
    from app.core.dependencies import ensure_idempotency  # lazy import to avoid cycles

    return ensure_idempotency(*args, **kwargs)


def ensure_idempotency_replay_dep(enforcer: IdempotencyEnforcer | None = None, *args, **kwargs):
    if enforcer is not None:
        return enforcer.dependency(allow_replay=True)
    from app.core.dependencies import ensure_idempotency_replay  # lazy import to avoid cycles

    return ensure_idempotency_replay(*args, **kwargs)


__all__ = ["IdempotencyEnforcer", "ensure_idempotency_dep", "ensure_idempotency_replay_dep"]
