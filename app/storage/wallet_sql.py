# app/storage/wallet_sql.py
from __future__ import annotations

import logging
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Optional

from sqlalchemy import (
    Column,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    Numeric,
    String,
    Table,
    UniqueConstraint,
    func,
    select,
    text,
)
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import Select

logger = logging.getLogger(__name__)

_DECIMAL_PLACES = 6


def _q(v: Decimal) -> Decimal:
    step = Decimal(1) / (Decimal(10) ** _DECIMAL_PLACES)
    return v.quantize(step, rounding=ROUND_HALF_UP)


def _to_decimal(v: Any) -> Decimal:
    if isinstance(v, Decimal):
        d = v
    else:
        try:
            d = Decimal(str(v))
        except (InvalidOperation, ValueError, TypeError):
            raise ValueError("invalid decimal value")
    return _q(d)


def _utcnow_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _norm_currency(code: str) -> str:
    if code is None:
        raise ValueError("currency required")
    v = str(code).strip().upper()
    if not (3 <= len(v) <= 10):
        raise ValueError("currency must be 3..10 chars")
    return v


metadata = MetaData()

wallet_accounts = Table(
    "wallet_accounts",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("user_id", Integer, nullable=False, index=True),
    Column("currency", String(10), nullable=False, index=True),
    Column("balance", Numeric(18, _DECIMAL_PLACES), nullable=False, server_default="0"),
    Column("created_at", String(40), nullable=False),
    Column("updated_at", String(40), nullable=False),
    UniqueConstraint("user_id", "currency", name="uq_wallet_accounts_user_currency"),
)

Index("ix_wallet_accounts_user_currency", wallet_accounts.c.user_id, wallet_accounts.c.currency)
Index("ix_wallet_accounts_updated_at", wallet_accounts.c.updated_at)

wallet_ledger = Table(
    "wallet_ledger",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("account_id", Integer, ForeignKey("wallet_accounts.id", ondelete="CASCADE"), nullable=False, index=True),
    Column("entry_type", String(20), nullable=False),
    Column("amount", Numeric(18, _DECIMAL_PLACES), nullable=False),
    Column("currency", String(10), nullable=False),
    Column("reference", String(255), nullable=True),
    Column("client_request_id", String(128), nullable=True, index=True),
    Column("created_at", String(40), nullable=False),
)

Index("ix_wallet_ledger_account_id", wallet_ledger.c.account_id)
Index("ix_wallet_ledger_created_at", wallet_ledger.c.created_at)
Index("ix_wallet_ledger_client_request_id", wallet_ledger.c.client_request_id)


def _is_sqlite_engine(db: AsyncSession) -> bool:
    try:
        bind = db.get_bind()
        if bind is None:
            return False
        return (bind.dialect.name or "").lower() == "sqlite"
    except Exception:
        return False


def _is_postgres_engine(db: AsyncSession) -> bool:
    try:
        bind = db.get_bind()
        if bind is None:
            return False
        return (bind.dialect.name or "").lower() == "postgresql"
    except Exception:
        return False


def _normalize_client_request_id(value: Optional[str]) -> Optional[str]:
    v = (value or "").strip()
    if not v:
        return None
    return v[:128]


def _with_for_update(db: AsyncSession, stmt: Select) -> Select:
    try:
        if _is_sqlite_engine(db):
            return stmt
        return stmt.with_for_update(nowait=False, of=wallet_accounts)
    except Exception:
        return stmt


def _account_row_to_dict(row: Any) -> dict[str, Any]:
    return {
        "id": int(row["id"]),
        "user_id": int(row["user_id"]),
        "currency": str(row["currency"]).upper(),
        "balance": str(_to_decimal(row["balance"])),
        "created_at": str(row["created_at"]),
        "updated_at": str(row["updated_at"]),
    }


def _ledger_row_to_item(row: Any) -> dict[str, Any]:
    return {
        "id": int(row["id"]),
        "account_id": int(row["account_id"]),
        "type": str(row["entry_type"]),
        "amount": str(_to_decimal(row["amount"])),
        "currency": str(row["currency"]).upper(),
        "reference": row["reference"],
        "created_at": str(row["created_at"]),
    }


class WalletError(ValueError):
    pass


class NotFoundError(WalletError):
    pass


class InsufficientFundsError(WalletError):
    pass


class CurrencyMismatchError(WalletError):
    pass


class WalletStorageSQL:
    """Async production storage for wallet balances."""

    def __init__(self, db: AsyncSession) -> None:
        if db is None:
            raise RuntimeError("DB session is required for wallet storage")
        self._db = db

    async def _ensure_account_company(self, account_id: int, company_id: Optional[int]) -> None:
        if company_id is None:
            return
        row = (
            await self._db.execute(
                text(
                    "SELECT 1 FROM wallet_accounts wa JOIN users u ON u.id = wa.user_id "
                    "WHERE wa.id = :id AND u.company_id = :cid"
                ),
                {"id": account_id, "cid": company_id},
            )
        ).first()
        if not row:
            raise NotFoundError("account not found")

    async def create_account(
        self, user_id: int, currency: str, initial_balance: Optional[Decimal] = None
    ) -> dict[str, Any]:
        cur = _norm_currency(currency)
        now = _utcnow_iso()
        init_bal = _to_decimal(initial_balance or Decimal("0"))
        existing = (
            (
                await self._db.execute(
                    select(wallet_accounts).where(
                        wallet_accounts.c.user_id == user_id,
                        wallet_accounts.c.currency == cur,
                    )
                )
            )
            .mappings()
            .first()
        )
        if existing:
            return _account_row_to_dict(existing)

        await self._db.execute(
            wallet_accounts.insert().values(
                user_id=user_id,
                currency=cur,
                balance=init_bal,
                created_at=now,
                updated_at=now,
            )
        )
        row = (
            (
                await self._db.execute(
                    select(wallet_accounts).where(
                        wallet_accounts.c.user_id == user_id,
                        wallet_accounts.c.currency == cur,
                    )
                )
            )
            .mappings()
            .first()
        )
        return _account_row_to_dict(row)

    async def get_account(self, account_id: int, *, company_id: Optional[int] = None) -> Optional[dict[str, Any]]:
        await self._ensure_account_company(account_id, company_id)
        if company_id is None:
            stmt = select(wallet_accounts).where(wallet_accounts.c.id == account_id)
            row = (await self._db.execute(stmt)).mappings().first()
        else:
            row = (
                (
                    await self._db.execute(
                        text(
                            "SELECT wa.* FROM wallet_accounts wa JOIN users u ON u.id = wa.user_id "
                            "WHERE wa.id = :id AND u.company_id = :cid"
                        ),
                        {"id": account_id, "cid": company_id},
                    )
                )
                .mappings()
                .first()
            )
        return _account_row_to_dict(row) if row else None

    async def get_account_by_user_currency(self, user_id: int, currency: str) -> Optional[dict[str, Any]]:
        cur = _norm_currency(currency)
        row = (
            (
                await self._db.execute(
                    select(wallet_accounts).where(
                        wallet_accounts.c.user_id == user_id,
                        wallet_accounts.c.currency == cur,
                    )
                )
            )
            .mappings()
            .first()
        )
        return _account_row_to_dict(row) if row else None

    async def list_accounts(
        self,
        user_id: Optional[int] = None,
        *,
        user_ids: Optional[list[int]] = None,
        company_id: Optional[int] = None,
    ) -> list[dict[str, Any]]:
        if company_id is not None:
            base_sql = "SELECT wa.* FROM wallet_accounts wa JOIN users u ON u.id = wa.user_id WHERE 1=1"
            params: dict[str, Any] = {}
            if user_id is not None:
                base_sql += " AND wa.user_id = :uid"
                params["uid"] = user_id
            if user_ids:
                base_sql += " AND wa.user_id = ANY(:uids)"
                params["uids"] = user_ids
            base_sql += " AND u.company_id = :cid ORDER BY wa.id DESC"
            params["cid"] = company_id
            rows = (await self._db.execute(text(base_sql), params)).mappings().all()
            return [_account_row_to_dict(r) for r in rows]

        q = select(wallet_accounts)
        if user_id is not None:
            q = q.where(wallet_accounts.c.user_id == user_id)
        if user_ids:
            q = q.where(wallet_accounts.c.user_id.in_(user_ids))
        rows = (await self._db.execute(q.order_by(wallet_accounts.c.id.desc()))).mappings().all()
        return [_account_row_to_dict(r) for r in rows]

    async def get_balance(self, account_id: int, *, company_id: Optional[int] = None) -> dict[str, Any]:
        await self._ensure_account_company(account_id, company_id)
        row = (
            (
                await self._db.execute(
                    select(
                        wallet_accounts.c.id,
                        wallet_accounts.c.balance,
                        wallet_accounts.c.currency,
                    ).where(wallet_accounts.c.id == account_id)
                )
            )
            .mappings()
            .first()
        )
        if not row:
            raise NotFoundError("account not found")
        return {
            "account_id": int(row["id"]),
            "balance": str(_to_decimal(row["balance"])),
            "currency": str(row["currency"]).upper(),
        }

    async def deposit(
        self,
        account_id: int,
        amount: Decimal,
        reference: Optional[str],
        client_request_id: str | None = None,
        *,
        company_id: Optional[int] = None,
    ) -> dict[str, Any]:
        amt = _to_decimal(amount)
        if amt <= 0:
            raise WalletError("amount must be positive")
        now = _utcnow_iso()
        ref = (reference or "").strip() or None
        if ref:
            ref = ref[:255]
        req_id = _normalize_client_request_id(client_request_id)

        await self._ensure_account_company(account_id, company_id)
        stmt = _with_for_update(self._db, select(wallet_accounts).where(wallet_accounts.c.id == account_id))
        acc = (await self._db.execute(stmt)).mappings().first()
        if not acc:
            raise NotFoundError("account not found")

        new_bal = _to_decimal(acc["balance"]) + amt
        if req_id and _is_postgres_engine(self._db):
            ins = (
                pg_insert(wallet_ledger)
                .values(
                    account_id=account_id,
                    entry_type="deposit",
                    amount=amt,
                    currency=acc["currency"],
                    reference=ref,
                    client_request_id=req_id,
                    created_at=now,
                )
                .on_conflict_do_nothing(
                    index_elements=["account_id", "client_request_id"],
                    index_where=text("client_request_id IS NOT NULL"),
                )
            )
            res = await self._db.execute(ins)
            if not (res.rowcount or 0):
                row = (
                    await self._db.execute(select(wallet_accounts).where(wallet_accounts.c.id == account_id))
                ).mappings().first()
                if not row:
                    raise NotFoundError("account not found")
                return {
                    "account_id": int(account_id),
                    "balance": str(_to_decimal(row["balance"])),
                    "currency": str(row["currency"]).upper(),
                }

            await self._db.execute(
                wallet_accounts.update()
                .where(wallet_accounts.c.id == account_id)
                .values(balance=new_bal, updated_at=now)
            )
        else:
            await self._db.execute(
                wallet_accounts.update()
                .where(wallet_accounts.c.id == account_id)
                .values(balance=new_bal, updated_at=now)
            )
            await self._db.execute(
                wallet_ledger.insert().values(
                    account_id=account_id,
                    entry_type="deposit",
                    amount=amt,
                    currency=acc["currency"],
                    reference=ref,
                    client_request_id=req_id,
                    created_at=now,
                )
            )
        return {
            "account_id": int(account_id),
            "balance": str(new_bal),
            "currency": str(acc["currency"]).upper(),
        }

    async def withdraw(
        self,
        account_id: int,
        amount: Decimal,
        reference: Optional[str],
        client_request_id: str | None = None,
        *,
        company_id: Optional[int] = None,
    ) -> dict[str, Any]:
        amt = _to_decimal(amount)
        if amt <= 0:
            raise WalletError("amount must be positive")
        now = _utcnow_iso()
        ref = (reference or "").strip() or None
        if ref:
            ref = ref[:255]
        req_id = _normalize_client_request_id(client_request_id)

        await self._ensure_account_company(account_id, company_id)
        stmt = _with_for_update(self._db, select(wallet_accounts).where(wallet_accounts.c.id == account_id))
        acc = (await self._db.execute(stmt)).mappings().first()
        if not acc:
            raise NotFoundError("account not found")

        cur_bal = _to_decimal(acc["balance"])
        if cur_bal < amt:
            raise InsufficientFundsError("insufficient funds")
        new_bal = cur_bal - amt

        if req_id and _is_postgres_engine(self._db):
            ins = (
                pg_insert(wallet_ledger)
                .values(
                    account_id=account_id,
                    entry_type="withdraw",
                    amount=amt,
                    currency=acc["currency"],
                    reference=ref,
                    client_request_id=req_id,
                    created_at=now,
                )
                .on_conflict_do_nothing(
                    index_elements=["account_id", "client_request_id"],
                    index_where=text("client_request_id IS NOT NULL"),
                )
            )
            res = await self._db.execute(ins)
            if not (res.rowcount or 0):
                row = (
                    await self._db.execute(select(wallet_accounts).where(wallet_accounts.c.id == account_id))
                ).mappings().first()
                if not row:
                    raise NotFoundError("account not found")
                return {
                    "account_id": int(account_id),
                    "balance": str(_to_decimal(row["balance"])),
                    "currency": str(row["currency"]).upper(),
                }

            await self._db.execute(
                wallet_accounts.update()
                .where(wallet_accounts.c.id == account_id)
                .values(balance=new_bal, updated_at=now)
            )
        else:
            await self._db.execute(
                wallet_accounts.update()
                .where(wallet_accounts.c.id == account_id)
                .values(balance=new_bal, updated_at=now)
            )
            await self._db.execute(
                wallet_ledger.insert().values(
                    account_id=account_id,
                    entry_type="withdraw",
                    amount=amt,
                    currency=acc["currency"],
                    reference=ref,
                    client_request_id=req_id,
                    created_at=now,
                )
            )
        return {
            "account_id": int(account_id),
            "balance": str(new_bal),
            "currency": str(acc["currency"]).upper(),
        }

    async def transfer(
        self,
        src_account_id: int,
        dst_account_id: int,
        amount: Decimal,
        reference: Optional[str],
        client_request_id: str | None = None,
        *,
        company_id: Optional[int] = None,
    ) -> dict[str, Any]:
        if src_account_id == dst_account_id:
            raise WalletError("source and destination must differ")
        amt = _to_decimal(amount)
        if amt <= 0:
            raise WalletError("amount must be positive")
        now = _utcnow_iso()
        ref = (reference or "").strip() or None
        if ref:
            ref = ref[:255]
        req_id = _normalize_client_request_id(client_request_id)

        await self._ensure_account_company(src_account_id, company_id)
        await self._ensure_account_company(dst_account_id, company_id)
        a, b = (src_account_id, dst_account_id) if src_account_id < dst_account_id else (dst_account_id, src_account_id)
        stmt = _with_for_update(self._db, select(wallet_accounts).where(wallet_accounts.c.id.in_([a, b])))
        locked = (await self._db.execute(stmt)).mappings().all()
        accs = {int(r["id"]): dict(r) for r in locked}

        src = accs.get(src_account_id)
        dst = accs.get(dst_account_id)
        if not src or not dst:
            raise NotFoundError("source or destination account not found")

        src_cur = str(src["currency"]).upper()
        dst_cur = str(dst["currency"]).upper()
        if src_cur != dst_cur:
            raise CurrencyMismatchError("currency mismatch")

        src_bal = _to_decimal(src["balance"])
        if src_bal < amt:
            raise InsufficientFundsError("insufficient funds")

        src_bal_new = src_bal - amt
        dst_bal_new = _to_decimal(dst["balance"]) + amt

        if req_id and _is_postgres_engine(self._db):
            out_ins = (
                pg_insert(wallet_ledger)
                .values(
                    account_id=src_account_id,
                    entry_type="transfer_out",
                    amount=amt,
                    currency=src_cur,
                    reference=ref,
                    client_request_id=req_id,
                    created_at=now,
                )
                .on_conflict_do_nothing(
                    index_elements=["account_id", "client_request_id"],
                    index_where=text("client_request_id IS NOT NULL"),
                )
            )
            in_ins = (
                pg_insert(wallet_ledger)
                .values(
                    account_id=dst_account_id,
                    entry_type="transfer_in",
                    amount=amt,
                    currency=dst_cur,
                    reference=ref,
                    client_request_id=req_id,
                    created_at=now,
                )
                .on_conflict_do_nothing(
                    index_elements=["account_id", "client_request_id"],
                    index_where=text("client_request_id IS NOT NULL"),
                )
            )
            res_out = await self._db.execute(out_ins)
            res_in = await self._db.execute(in_ins)
            out_row = res_out.rowcount or 0
            in_row = res_in.rowcount or 0

            if out_row == 0 and in_row == 0:
                src_row = (
                    await self._db.execute(select(wallet_accounts).where(wallet_accounts.c.id == src_account_id))
                ).mappings().first()
                dst_row = (
                    await self._db.execute(select(wallet_accounts).where(wallet_accounts.c.id == dst_account_id))
                ).mappings().first()
                if not src_row or not dst_row:
                    raise NotFoundError("source or destination account not found")
                return {
                    "source": {
                        "account_id": int(src_account_id),
                        "balance": str(_to_decimal(src_row["balance"])),
                        "currency": str(src_row["currency"]).upper(),
                    },
                    "destination": {
                        "account_id": int(dst_account_id),
                        "balance": str(_to_decimal(dst_row["balance"])),
                        "currency": str(dst_row["currency"]).upper(),
                    },
                }

            if out_row != in_row:
                raise WalletError("transfer idempotency conflict")

            await self._db.execute(
                wallet_accounts.update()
                .where(wallet_accounts.c.id == src_account_id)
                .values(balance=src_bal_new, updated_at=now)
            )
            await self._db.execute(
                wallet_accounts.update()
                .where(wallet_accounts.c.id == dst_account_id)
                .values(balance=dst_bal_new, updated_at=now)
            )
        else:
            await self._db.execute(
                wallet_accounts.update()
                .where(wallet_accounts.c.id == src_account_id)
                .values(balance=src_bal_new, updated_at=now)
            )
            await self._db.execute(
                wallet_accounts.update()
                .where(wallet_accounts.c.id == dst_account_id)
                .values(balance=dst_bal_new, updated_at=now)
            )

            await self._db.execute(
                wallet_ledger.insert().values(
                    account_id=src_account_id,
                    entry_type="transfer_out",
                    amount=amt,
                    currency=src_cur,
                    reference=ref,
                    client_request_id=req_id,
                    created_at=now,
                )
            )
            await self._db.execute(
                wallet_ledger.insert().values(
                    account_id=dst_account_id,
                    entry_type="transfer_in",
                    amount=amt,
                    currency=dst_cur,
                    reference=ref,
                    client_request_id=req_id,
                    created_at=now,
                )
            )

        return {
            "source": {"account_id": int(src_account_id), "balance": str(src_bal_new), "currency": src_cur},
            "destination": {"account_id": int(dst_account_id), "balance": str(dst_bal_new), "currency": dst_cur},
        }

    async def adjust_balance(
        self,
        account_id: int,
        new_balance: Decimal,
        reference: Optional[str],
        client_request_id: str | None = None,
        *,
        company_id: Optional[int] = None,
    ) -> dict[str, Any]:
        nb = _to_decimal(new_balance)
        now = _utcnow_iso()
        ref = (reference or "").strip() or None
        if ref:
            ref = ref[:255]
        req_id = _normalize_client_request_id(client_request_id)

        await self._ensure_account_company(account_id, company_id)
        stmt = _with_for_update(self._db, select(wallet_accounts).where(wallet_accounts.c.id == account_id))
        acc = (await self._db.execute(stmt)).mappings().first()
        if not acc:
            raise NotFoundError("account not found")

        old = _to_decimal(acc["balance"])
        delta = nb - old
        if delta == 0:
            return {"account_id": int(account_id), "balance": str(old), "currency": str(acc["currency"]).upper()}

        if req_id and _is_postgres_engine(self._db):
            ins = (
                pg_insert(wallet_ledger)
                .values(
                    account_id=account_id,
                    entry_type="adjustment",
                    amount=_to_decimal(delta.copy_abs()),
                    currency=acc["currency"],
                    reference=(ref or f"adjust: {old} -> {nb}")[:255],
                    client_request_id=req_id,
                    created_at=now,
                )
                .on_conflict_do_nothing(
                    index_elements=["account_id", "client_request_id"],
                    index_where=text("client_request_id IS NOT NULL"),
                )
            )
            res = await self._db.execute(ins)
            if not (res.rowcount or 0):
                row = (
                    await self._db.execute(select(wallet_accounts).where(wallet_accounts.c.id == account_id))
                ).mappings().first()
                if not row:
                    raise NotFoundError("account not found")
                return {
                    "account_id": int(account_id),
                    "balance": str(_to_decimal(row["balance"])),
                    "currency": str(row["currency"]).upper(),
                }

            await self._db.execute(
                wallet_accounts.update()
                .where(wallet_accounts.c.id == account_id)
                .values(balance=nb, updated_at=now)
            )
        else:
            await self._db.execute(
                wallet_accounts.update()
                .where(wallet_accounts.c.id == account_id)
                .values(balance=nb, updated_at=now)
            )
            await self._db.execute(
                wallet_ledger.insert().values(
                    account_id=account_id,
                    entry_type="adjustment",
                    amount=_to_decimal(delta.copy_abs()),
                    currency=acc["currency"],
                    reference=(ref or f"adjust: {old} -> {nb}")[:255],
                    client_request_id=req_id,
                    created_at=now,
                )
            )
        return {"account_id": int(account_id), "balance": str(nb), "currency": str(acc["currency"]).upper()}

    async def list_ledger(
        self,
        account_id: int,
        page: int,
        size: int,
        *,
        company_id: Optional[int] = None,
    ) -> dict[str, Any]:
        if page < 1:
            page = 1
        if size < 1:
            size = 20
        if size > 200:
            size = 200
        off = (page - 1) * size
        await self._ensure_account_company(account_id, company_id)
        base_stmt = select(wallet_ledger).where(wallet_ledger.c.account_id == account_id)
        total_stmt = select(func.count()).select_from(wallet_ledger).where(wallet_ledger.c.account_id == account_id)

        total = (await self._db.execute(total_stmt)).scalar_one()
        rows = (
            (await self._db.execute(base_stmt.order_by(wallet_ledger.c.id.desc()).offset(off).limit(size)))
            .mappings()
            .all()
        )

        items = [_ledger_row_to_item(r) for r in rows]
        return {
            "items": items,
            "meta": {"page": int(page), "size": int(size), "total": int(total or 0)},
        }

    async def health(self) -> dict[str, Any]:
        try:
            await self._db.execute(select(func.count()).select_from(wallet_accounts))
            await self._db.execute(select(func.count()).select_from(wallet_ledger))
            bind = self._db.get_bind()
            engine_name = bind.dialect.name if bind else "unknown"
            return {"ok": True, "engine": engine_name}
        except Exception as e:
            logger.error("Wallet storage health error: %s", e)
            return {"ok": False, "error": str(e)}

    async def stats(self) -> dict[str, Any]:
        cnt_acc = (await self._db.execute(select(func.count()).select_from(wallet_accounts))).scalar_one()
        cnt_led = (await self._db.execute(select(func.count()).select_from(wallet_ledger))).scalar_one()
        total_balance = (
            await self._db.execute(select(func.coalesce(func.sum(wallet_accounts.c.balance), 0)))
        ).scalar_one()
        return {
            "accounts": int(cnt_acc or 0),
            "ledger_entries": int(cnt_led or 0),
            "total_balance": str(_to_decimal(total_balance or 0)),
        }
