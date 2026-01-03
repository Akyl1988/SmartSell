# app/storage/wallet_sql.py
from __future__ import annotations

import logging
from contextlib import contextmanager, asynccontextmanager
from decimal import Decimal, ROUND_HALF_UP, InvalidOperation
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import (
    MetaData, Table, Column, Integer, String, Numeric,
    ForeignKey, select, func, Index, UniqueConstraint
)
from sqlalchemy.orm import Session
from sqlalchemy.sql import Select
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# =============================================================================
# Константы/вспомогалки для времени/денег
# =============================================================================

_DECIMAL_PLACES = 6  # для Numeric(18, 6)

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
    # ISO8601 без микросекунд, с 'Z' для однообразия в JSON
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

def _norm_currency(code: str) -> str:
    if code is None:
        raise ValueError("currency required")
    v = str(code).strip().upper()
    if not (3 <= len(v) <= 10):
        raise ValueError("currency must be 3..10 chars")
    return v

metadata = MetaData()
metadata = MetaData()

def _is_sqlite_engine(db: Session) -> bool:
    try:
        bind = db.get_bind()
        if bind is None:
            return False
        return (bind.dialect.name or "").lower() == "sqlite"
    except Exception:
        return False


@contextmanager
def _tx(session):
    """
    Safe transaction scope:
    - If a transaction is already active (common in tests / request-scoped sessions),
      use SAVEPOINT via begin_nested().
    - Otherwise start a normal transaction.
    """
    if session.get_transaction() is not None:
        with session.begin_nested():
            yield
    else:
        with session.begin():
            yield

# =============================================================================
# Схема (SQLAlchemy Core)
# =============================================================================

wallet_accounts = Table(
    "wallet_accounts",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("user_id", Integer, nullable=False, index=True),
    Column("currency", String(10), nullable=False, index=True),
    Column("balance", Numeric(18, _DECIMAL_PLACES), nullable=False, server_default="0"),
    # храним ISO-строки — единый контракт между БД
    Column("created_at", String(40), nullable=False),
    Column("updated_at", String(40), nullable=False),
    UniqueConstraint("user_id", "currency", name="uq_wallet_accounts_user_currency"),
)

# частая выборка по пользователю и валюте
Index("ix_wallet_accounts_user_currency", wallet_accounts.c.user_id, wallet_accounts.c.currency)
# быстрые выборки по updated_at (проектная заделка)
Index("ix_wallet_accounts_updated_at", wallet_accounts.c.updated_at)

wallet_ledger = Table(
    "wallet_ledger",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("account_id", Integer, ForeignKey("wallet_accounts.id", ondelete="CASCADE"), nullable=False, index=True),
    Column("entry_type", String(20), nullable=False),  # deposit|withdraw|transfer_in|transfer_out|adjustment
    Column("amount", Numeric(18, _DECIMAL_PLACES), nullable=False),
    Column("currency", String(10), nullable=False),
    Column("reference", String(255), nullable=True),
    Column("created_at", String(40), nullable=False),
)

Index("ix_wallet_ledger_account_id", wallet_ledger.c.account_id)
Index("ix_wallet_ledger_created_at", wallet_ledger.c.created_at)

# =============================================================================
# Сессия
# =============================================================================

@contextmanager
def session_scope():
    _, maker = _ensure_engine_and_session()
    s = maker()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()

# =============================================================================
# Маппинг рядов БД -> dict API
# =============================================================================

def _account_row_to_dict(row: Any) -> Dict[str, Any]:
    return {
        "id": int(row["id"]),
        "user_id": int(row["user_id"]),
        "currency": str(row["currency"]).upper(),
        "balance": str(_to_decimal(row["balance"])),
        "created_at": str(row["created_at"]),
        "updated_at": str(row["updated_at"]),
    }

def _ledger_row_to_item(row: Any) -> Dict[str, Any]:
    return {
        "id": int(row["id"]),
        "account_id": int(row["account_id"]),
        "type": str(row["entry_type"]),
        "amount": str(_to_decimal(row["amount"])),
        "currency": str(row["currency"]).upper(),
        "reference": row["reference"],
        "created_at": str(row["created_at"]),
    }

# =============================================================================
# Ошибки (базируемся на ValueError для обратной совместимости)
# =============================================================================

class WalletError(ValueError):
    pass

class NotFoundError(WalletError):
    pass

class InsufficientFundsError(WalletError):
    pass

class CurrencyMismatchError(WalletError):
    pass

# =============================================================================
# Вспомогалки для блокировок
# =============================================================================

def _with_for_update(db: Session, stmt: Select) -> Select:
    """
    Для PostgreSQL включаем row-level lock; для SQLite — игнорируем (нет поддержки).
    """
    try:
        if _is_sqlite_engine(db):
            return stmt  # SQLite не поддерживает FOR UPDATE
        return stmt.with_for_update(nowait=False, of=wallet_accounts)
    except Exception:
        return stmt

# =============================================================================
# Storage API
# =============================================================================

class WalletStorageSQL:
    """Production storage для кошельков/балансов (SQLAlchemy Core, транзакции)."""

    def __init__(self, db: Session) -> None:
        if db is None:
            raise RuntimeError("DB session is required for wallet storage")
        self._db = db

    # ------------------------ Аккаунты ----------------------------------------

    def create_account(self, user_id: int, currency: str, initial_balance: Optional[Decimal] = None) -> Dict[str, Any]:
        """
        Идемпотентное создание (апсерт по user_id+currency).
        Если аккаунт уже есть — возвращаем его.
        Можно задать начальный баланс (по умолчанию 0).
        """
        cur = _norm_currency(currency)
        now = _utcnow_iso()
        init_bal = _to_decimal(initial_balance or Decimal("0"))
        with _tx(self._db):
            existing = self._db.execute(
                select(wallet_accounts).where(
                    wallet_accounts.c.user_id == user_id,
                    wallet_accounts.c.currency == cur,
                )
            ).mappings().first()
            if existing:
                return _account_row_to_dict(existing)

            self._db.execute(
                wallet_accounts.insert().values(
                    user_id=user_id,
                    currency=cur,
                    balance=init_bal,
                    created_at=now,
                    updated_at=now,
                )
            )
            row = self._db.execute(
                select(wallet_accounts).where(
                    wallet_accounts.c.user_id == user_id,
                    wallet_accounts.c.currency == cur,
                )
            ).mappings().first()
            return _account_row_to_dict(row)

    def get_account(self, account_id: int) -> Optional[Dict[str, Any]]:
        row = self._db.execute(select(wallet_accounts).where(wallet_accounts.c.id == account_id)).mappings().first()
        return _account_row_to_dict(row) if row else None

    def get_account_by_user_currency(self, user_id: int, currency: str) -> Optional[Dict[str, Any]]:
        cur = _norm_currency(currency)
        row = self._db.execute(
                select(wallet_accounts).where(
                    wallet_accounts.c.user_id == user_id,
                    wallet_accounts.c.currency == cur,
                )
            ).mappings().first()
        return _account_row_to_dict(row) if row else None

    def list_accounts(
        self,
        user_id: Optional[int] = None,
        *,
        user_ids: Optional[List[int]] = None,
    ) -> List[Dict[str, Any]]:
        q = select(wallet_accounts)
        if user_id is not None:
            q = q.where(wallet_accounts.c.user_id == user_id)
        if user_ids:
            q = q.where(wallet_accounts.c.user_id.in_(user_ids))
        rows = self._db.execute(q.order_by(wallet_accounts.c.id.desc())).mappings().all()
        return [_account_row_to_dict(r) for r in rows]

    # ------------------------ Баланс / операции -------------------------------

    def get_balance(self, account_id: int) -> Dict[str, Any]:
        row = self._db.execute(
            select(
                wallet_accounts.c.id,
                wallet_accounts.c.balance,
                wallet_accounts.c.currency,
            ).where(wallet_accounts.c.id == account_id)
        ).mappings().first()
        if not row:
            raise NotFoundError("account not found")
        return {
            "account_id": int(row["id"]),
            "balance": str(_to_decimal(row["balance"])),
            "currency": str(row["currency"]).upper(),
        }

    def deposit(self, account_id: int, amount: Decimal, reference: Optional[str]) -> Dict[str, Any]:
        amt = _to_decimal(amount)
        if amt <= 0:
            raise WalletError("amount must be positive")
        now = _utcnow_iso()
        ref = (reference or "").strip() or None
        if ref:
            ref = ref[:255]

        with _tx(self._db):
            stmt = _with_for_update(self._db, select(wallet_accounts).where(wallet_accounts.c.id == account_id))
            acc = self._db.execute(stmt).mappings().first()
            if not acc:
                raise NotFoundError("account not found")

            new_bal = _to_decimal(acc["balance"]) + amt
            self._db.execute(
                wallet_accounts.update()
                .where(wallet_accounts.c.id == account_id)
                .values(balance=new_bal, updated_at=now)
            )
            self._db.execute(
                wallet_ledger.insert().values(
                    account_id=account_id,
                    entry_type="deposit",
                    amount=amt,
                    currency=acc["currency"],
                    reference=ref,
                    created_at=now,
                )
            )
            return {
                "account_id": int(account_id),
                "balance": str(new_bal),
                "currency": str(acc["currency"]).upper(),
            }

    def withdraw(self, account_id: int, amount: Decimal, reference: Optional[str]) -> Dict[str, Any]:
        amt = _to_decimal(amount)
        if amt <= 0:
            raise WalletError("amount must be positive")
        now = _utcnow_iso()
        ref = (reference or "").strip() or None
        if ref:
            ref = ref[:255]

        with _tx(self._db):
            stmt = _with_for_update(self._db, select(wallet_accounts).where(wallet_accounts.c.id == account_id))
            acc = self._db.execute(stmt).mappings().first()
            if not acc:
                raise NotFoundError("account not found")

            cur_bal = _to_decimal(acc["balance"])
            if cur_bal < amt:
                raise InsufficientFundsError("insufficient funds")
            new_bal = cur_bal - amt

            self._db.execute(
                wallet_accounts.update()
                .where(wallet_accounts.c.id == account_id)
                .values(balance=new_bal, updated_at=now)
            )
            self._db.execute(
                wallet_ledger.insert().values(
                    account_id=account_id,
                    entry_type="withdraw",
                    amount=amt,
                    currency=acc["currency"],
                    reference=ref,
                    created_at=now,
                )
            )
            return {
                "account_id": int(account_id),
                "balance": str(new_bal),
                "currency": str(acc["currency"]).upper(),
            }

    def transfer(self, src_account_id: int, dst_account_id: int, amount: Decimal, reference: Optional[str]) -> Dict[str, Any]:
        if src_account_id == dst_account_id:
            raise WalletError("source and destination must differ")
        amt = _to_decimal(amount)
        if amt <= 0:
            raise WalletError("amount must be positive")
        now = _utcnow_iso()
        ref = (reference or "").strip() or None
        if ref:
            ref = ref[:255]

        with _tx(self._db):
            # Стабильный порядок блокировок для противодействия дедлокам
            a, b = (src_account_id, dst_account_id) if src_account_id < dst_account_id else (dst_account_id, src_account_id)
            stmt = _with_for_update(self._db, select(wallet_accounts).where(wallet_accounts.c.id.in_([a, b])))
            locked = self._db.execute(stmt).mappings().all()
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

            self._db.execute(
                wallet_accounts.update()
                .where(wallet_accounts.c.id == src_account_id)
                .values(balance=src_bal_new, updated_at=now)
            )
            self._db.execute(
                wallet_accounts.update()
                .where(wallet_accounts.c.id == dst_account_id)
                .values(balance=dst_bal_new, updated_at=now)
            )

            self._db.execute(
                wallet_ledger.insert().values(
                    account_id=src_account_id, entry_type="transfer_out",
                    amount=amt, currency=src_cur, reference=ref, created_at=now
                )
            )
            self._db.execute(
                wallet_ledger.insert().values(
                    account_id=dst_account_id, entry_type="transfer_in",
                    amount=amt, currency=dst_cur, reference=ref, created_at=now
                )
            )

            return {
                "source": {"account_id": int(src_account_id), "balance": str(src_bal_new), "currency": src_cur},
                "destination": {"account_id": int(dst_account_id), "balance": str(dst_bal_new), "currency": dst_cur},
            }

    def adjust_balance(self, account_id: int, new_balance: Decimal, reference: Optional[str]) -> Dict[str, Any]:
        """
        Административная правка баланса (ledger: adjustment).
        Использовать осознанно — сумма коррекции = new - old.
        """
        nb = _to_decimal(new_balance)
        now = _utcnow_iso()
        ref = (reference or "").strip() or None
        if ref:
            ref = ref[:255]

        with _tx(self._db):
            stmt = _with_for_update(self._db, select(wallet_accounts).where(wallet_accounts.c.id == account_id))
            acc = self._db.execute(stmt).mappings().first()
            if not acc:
                raise NotFoundError("account not found")

            old = _to_decimal(acc["balance"])
            delta = nb - old
            if delta == 0:
                # Ничего менять не будем, но это не ошибка
                return {"account_id": int(account_id), "balance": str(old), "currency": str(acc["currency"]).upper()}

            self._db.execute(
                wallet_accounts.update()
                .where(wallet_accounts.c.id == account_id)
                .values(balance=nb, updated_at=now)
            )
            self._db.execute(
                wallet_ledger.insert().values(
                    account_id=account_id,
                    entry_type="adjustment",
                    amount=_to_decimal(delta.copy_abs()),
                    currency=acc["currency"],
                    reference=(ref or f"adjust: {old} -> {nb}")[:255],
                    created_at=now,
                )
            )
            return {"account_id": int(account_id), "balance": str(nb), "currency": str(acc["currency"]).upper()}

    # ------------------------ Ледгер / пагинация ------------------------------

    def list_ledger(self, account_id: int, page: int, size: int) -> Dict[str, Any]:
        if page < 1:
            page = 1
        if size < 1:
            size = 20
        if size > 200:
            size = 200
        off = (page - 1) * size

        total = self._db.execute(
                select(func.count()).select_from(wallet_ledger).where(wallet_ledger.c.account_id == account_id)
            ).scalar_one()
        rows = self._db.execute(
            select(wallet_ledger)
            .where(wallet_ledger.c.account_id == account_id)
            .order_by(wallet_ledger.c.id.desc())
            .offset(off)
            .limit(size)
        ).mappings().all()

        items = [_ledger_row_to_item(r) for r in rows]
        return {
            "items": items,
            "meta": {"page": int(page), "size": int(size), "total": int(total or 0)},
        }

    # ------------------------ Здоровье/статистика -----------------------------

    def health(self) -> Dict[str, Any]:
        """
        Быстрый health-check хранилища.
        """
        try:
            bind = self._db.get_bind()
            if bind is None:
                raise RuntimeError("DB bind not available")
            with bind.connect() as conn:
                conn.execute(select(func.count()).select_from(wallet_accounts))
                conn.execute(select(func.count()).select_from(wallet_ledger))
            return {"ok": True, "engine": bind.dialect.name}
        except Exception as e:
            logger.error("Wallet storage health error: %s", e)
            return {"ok": False, "error": str(e)}

    def stats(self) -> Dict[str, Any]:
        """
        Лёгкая статистика: количества аккаунтов/записей ледгера, суммарный баланс.
        """
        with session_scope() as s:
            cnt_acc = s.execute(select(func.count()).select_from(wallet_accounts)).scalar_one()
            cnt_led = s.execute(select(func.count()).select_from(wallet_ledger)).scalar_one()
            # суммарный баланс по всем аккаунтам (как строка)
            # примечание: для SQLite SUM(Numeric) может вернуться как float — приводим через _to_decimal
            total_balance = s.execute(select(func.coalesce(func.sum(wallet_accounts.c.balance), 0))).scalar_one()
            return {
                "accounts": int(cnt_acc or 0),
                "ledger_entries": int(cnt_led or 0),
                "total_balance": str(_to_decimal(total_balance or 0)),
            }
