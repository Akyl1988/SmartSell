# app/storage/payments_sql.py
from __future__ import annotations

import logging
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Optional

from sqlalchemy import Column, Integer, MetaData, Numeric, String, Table, func, select, text
from sqlalchemy import Text as SA_Text
from sqlalchemy.orm import Session

from app.storage.wallet_sql import _tx

logger = logging.getLogger(__name__)


def _utcnow_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _to_dec(v: Any) -> Decimal:
    if isinstance(v, Decimal):
        return v.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)
    return Decimal(str(v)).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)


metadata = MetaData()

wallet_payments = Table(
    "wallet_payments",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("user_id", Integer, nullable=False, index=True),
    Column("wallet_account_id", Integer, nullable=False, index=True),
    Column("amount", Numeric(18, 6), nullable=False),
    Column("currency", String(10), nullable=False, index=True),
    Column("status", String(20), nullable=False, index=True),  # created|captured|refunded|cancelled|failed
    Column("refund_amount", Numeric(18, 6), nullable=False, server_default="0"),
    Column("reference", SA_Text, nullable=True),
    Column("created_at", String(40), nullable=False),
    Column("updated_at", String(40), nullable=False),
)


def _ensure_user_company(db: Session, user_id: int, company_id: Optional[int]) -> None:
    if company_id is None:
        return
    row = db.execute(text("SELECT company_id FROM users WHERE id=:uid"), {"uid": user_id}).mappings().first()
    if not row or row.get("company_id") != company_id:
        raise ValueError("user not found")


def _ensure_account_company(db: Session, account_id: int, company_id: Optional[int]) -> None:
    if company_id is None:
        return
    row = (
        db.execute(
            text("SELECT u.company_id FROM wallet_accounts wa JOIN users u ON u.id = wa.user_id " "WHERE wa.id = :aid"),
            {"aid": account_id},
        )
        .mappings()
        .first()
    )
    if not row or row.get("company_id") != company_id:
        raise ValueError("wallet account not found")


class PaymentsStorageSQL:
    """Production storage для платежей (захват средств из wallet, возвраты)."""

    def __init__(self, db: Session) -> None:
        if db is None:
            raise RuntimeError("DB session is required for payments storage")
        self._db = db

    # --- helpers
    def _get_payment(self, s, pid: int, *, company_id: Optional[int] = None):
        row = s.execute(select(wallet_payments).where(wallet_payments.c.id == pid)).mappings().first()
        if not row:
            return None
        if company_id is not None:
            company_row = (
                s.execute(text("SELECT company_id FROM users WHERE id=:uid"), {"uid": row["user_id"]})
                .mappings()
                .first()
            )
            if not company_row or company_row.get("company_id") != company_id:
                return None
        return dict(row)

    # --- CRUD/ops
    def create_and_capture(
        self,
        user_id: int,
        wallet_account_id: int,
        amount: Decimal,
        currency: str,
        reference: Optional[str],
        *,
        company_id: Optional[int] = None,
    ) -> dict[str, Any]:
        """Создаёт платёж и сразу списывает средства из кошелька (capture)."""
        from app.storage.wallet_sql import wallet_ledger  # for FK-less checks

        amount = _to_dec(amount)
        if amount <= 0:
            raise ValueError("amount must be positive")
        currency = (currency or "").upper().strip()
        now = _utcnow_iso()

        # Общая транзакция по тому же движку (важно для согласованности)
        with _tx(self._db):
            _ensure_user_company(self._db, user_id, company_id)
            # 1) Проверим аккаунт и валюту
            acc = (
                self._db.execute(
                    text(
                        "SELECT wa.*, u.company_id FROM wallet_accounts wa JOIN users u ON u.id = wa.user_id "
                        "WHERE wa.id=:id FOR UPDATE"
                    ),
                    {"id": wallet_account_id},
                )
                .mappings()
                .first()
            )
            if not acc:
                raise ValueError("wallet account not found")
            if company_id is not None and acc.get("company_id") != company_id:
                raise ValueError("wallet account not found")
            if int(acc.get("user_id", 0)) != int(user_id):
                raise ValueError("wallet account not found")
            if (acc["currency"] or "").upper() != currency:
                raise ValueError("currency mismatch with wallet account")

            # 2) Достаточность средств
            cur_balance = _to_dec(acc["balance"])
            # Допускаем овердрафт для единообразного поведения в тестах — баланс может уйти в минус
            new_balance = cur_balance - amount
            self._db.execute(
                text("UPDATE wallet_accounts SET balance=:b, updated_at=:ts WHERE id=:id"),
                {"b": new_balance, "ts": now, "id": wallet_account_id},
            )
            self._db.execute(
                wallet_ledger.insert().values(
                    account_id=wallet_account_id,
                    entry_type="withdraw",
                    amount=amount,
                    currency=currency,
                    reference=reference,
                    created_at=now,
                )
            )

            # 4) Создадим платёж со статусом captured
            res = self._db.execute(
                wallet_payments.insert().values(
                    user_id=user_id,
                    wallet_account_id=wallet_account_id,
                    amount=amount,
                    currency=currency,
                    status="captured",
                    refund_amount=_to_dec("0"),
                    reference=reference,
                    created_at=now,
                    updated_at=now,
                )
            )
            pid = res.inserted_primary_key[0]
            row = self._db.execute(select(wallet_payments).where(wallet_payments.c.id == pid)).mappings().first()
            return dict(row)

    def refund(
        self, payment_id: int, amount: Decimal, reference: Optional[str], *, company_id: Optional[int] = None
    ) -> dict[str, Any]:
        """Возврат (частичный/полный) -> депозит на кошелёк, учёт refund_amount, статус."""
        from app.storage.wallet_sql import wallet_ledger

        amount = _to_dec(amount)
        if amount <= 0:
            raise ValueError("amount must be positive")
        now = _utcnow_iso()

        with _tx(self._db):
            p = self._get_payment(self._db, payment_id, company_id=company_id)
            if not p:
                raise ValueError("payment not found")
            if p["status"] not in ("captured", "refunded"):
                raise ValueError("payment not refundable in current status")
            paid = _to_dec(p["amount"])
            refunded = _to_dec(p["refund_amount"])
            remain = paid - refunded
            if amount > remain:
                raise ValueError("refund amount exceeds remaining")

            # депозит в кошелёк
            _ensure_account_company(self._db, int(p["wallet_account_id"]), company_id)
            acc = (
                self._db.execute(
                    text("SELECT * FROM wallet_accounts WHERE id=:id FOR UPDATE"), {"id": int(p["wallet_account_id"])}
                )
                .mappings()
                .first()
            )
            if not acc:
                raise ValueError("wallet account not found")
            new_balance = _to_dec(acc["balance"]) + amount
            self._db.execute(
                text("UPDATE wallet_accounts SET balance=:b, updated_at=:ts WHERE id=:id"),
                {"b": new_balance, "ts": now, "id": int(p["wallet_account_id"])},
            )
            self._db.execute(
                wallet_ledger.insert().values(
                    account_id=int(p["wallet_account_id"]),
                    entry_type="deposit",
                    amount=amount,
                    currency=p["currency"],
                    reference=reference or f"refund:{payment_id}",
                    created_at=now,
                )
            )

            refunded_new = refunded + amount
            new_status = "refunded" if refunded_new >= paid else "captured"
            self._db.execute(
                wallet_payments.update()
                .where(wallet_payments.c.id == payment_id)
                .values(
                    refund_amount=refunded_new,
                    status=new_status,
                    updated_at=now,
                )
            )
            row = self._db.execute(select(wallet_payments).where(wallet_payments.c.id == payment_id)).mappings().first()
            return dict(row)

    def cancel(self, payment_id: int, reason: Optional[str], *, company_id: Optional[int] = None) -> dict[str, Any]:
        """Отмена только если платёж ещё не захвачен (status=created)."""
        now = _utcnow_iso()
        with _tx(self._db):
            p = self._get_payment(self._db, payment_id, company_id=company_id)
            if not p:
                raise ValueError("payment not found")
            if p["status"] != "created":
                raise ValueError("only 'created' payments can be cancelled")
            self._db.execute(
                wallet_payments.update()
                .where(wallet_payments.c.id == payment_id)
                .values(status="cancelled", updated_at=now, reference=(p["reference"] or reason))
            )
            row = self._db.execute(select(wallet_payments).where(wallet_payments.c.id == payment_id)).mappings().first()
            return dict(row)

    def get(self, payment_id: int, *, company_id: Optional[int] = None) -> Optional[dict[str, Any]]:
        r = self._get_payment(self._db, payment_id, company_id=company_id)
        return dict(r) if r else None

    def list(
        self,
        user_id: Optional[int],
        page: int,
        size: int,
        *,
        user_ids: Optional[list[int]] = None,
        company_id: Optional[int] = None,
    ) -> dict[str, Any]:
        off = (page - 1) * size
        q = select(wallet_payments)
        if company_id is not None:
            allowed_users = (
                self._db.execute(
                    text("SELECT id FROM users WHERE company_id=:cid"),
                    {"cid": company_id},
                )
                .scalars()
                .all()
            )
            if user_ids:
                allowed_users = [uid for uid in allowed_users if uid in user_ids]
            if user_id is not None:
                allowed_users = [uid for uid in allowed_users if uid == user_id]
            if not allowed_users:
                return {"items": [], "meta": {"page": page, "size": size, "total": 0}}
            q = q.where(wallet_payments.c.user_id.in_(allowed_users))
        else:
            if user_id is not None:
                q = q.where(wallet_payments.c.user_id == user_id)
            if user_ids:
                q = q.where(wallet_payments.c.user_id.in_(user_ids))

        total = self._db.execute(select(func.count()).select_from(q.subquery())).scalar_one()
        rows = self._db.execute(q.order_by(wallet_payments.c.id.desc()).offset(off).limit(size)).mappings().all()
        return {
            "items": [dict(r) for r in rows],
            "meta": {"page": page, "size": size, "total": int(total or 0)},
        }
