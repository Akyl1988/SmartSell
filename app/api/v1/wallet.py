# app/api/v1/wallet.py
from __future__ import annotations

import inspect
import logging
from decimal import Decimal, InvalidOperation
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Path, Query, status
from pydantic import BaseModel, Field, conint, constr
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.security import get_current_user as get_current_user_security
from app.models.user import User

# =============================================================================
# ЛОГИРОВАНИЕ
# =============================================================================
logger = logging.getLogger(__name__)


async def _auth_user(
    token_data: dict = Depends(get_current_user_security),
    db: Session = Depends(get_db),
) -> User:
    sub = token_data.get("sub")
    try:
        user_id = int(sub)
    except Exception:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")

    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    return user


def require_role(*roles: str):
    async def dep(user: User = Depends(_auth_user)):
        if getattr(user, "role", None) not in roles:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="forbidden")
        return user

    return dep


# =============================================================================
# ХЕЛПЕРЫ
# =============================================================================
def _norm_ccy(code: str | None) -> str:
    v = (code or "").strip().upper()
    if not (3 <= len(v) <= 10):
        raise HTTPException(status_code=422, detail="currency must be 3..10 chars")
    return v


def _to_dec_str(v: Any) -> str:
    if isinstance(v, Decimal):
        return str(v.normalize()) if v == v.to_integral() else str(v)
    try:
        d = Decimal(str(v))
        return str(d.normalize()) if d == d.to_integral() else str(d)
    except (InvalidOperation, ValueError, TypeError):
        raise HTTPException(status_code=400, detail="invalid decimal")


def _pick_http_status(exc: Exception) -> int:
    if isinstance(exc, HTTPException):
        raise exc
    msg = str(exc).lower()
    if "not found" in msg:
        return status.HTTP_404_NOT_FOUND
    if "insufficient" in msg and "fund" in msg:
        return status.HTTP_409_CONFLICT
    if "currency mismatch" in msg:
        return status.HTTP_409_CONFLICT
    if "unique" in msg or "duplicate" in msg or "already exist" in msg or "conflict" in msg:
        return status.HTTP_409_CONFLICT
    return status.HTTP_400_BAD_REQUEST


def _safe_bool(v: Any, default: bool = False) -> bool:
    try:
        return bool(v)
    except Exception:
        return default


def _is_platform_admin(user: User | None) -> bool:
    try:
        return str(getattr(user, "role", "")).lower() == "platform_admin"
    except Exception:
        return False


async def _ensure_user_in_company(
    target_user_id: int,
    current_user: User,
    db: Session,
    *,
    not_found_detail: str = "account not found",
) -> User:
    user = db.get(User, target_user_id)
    if not user:
        raise HTTPException(status_code=404, detail=not_found_detail)
    if _is_platform_admin(current_user):
        return user
    if getattr(user, "company_id", None) != getattr(current_user, "company_id", None):
        raise HTTPException(status_code=404, detail=not_found_detail)
    return user


async def _load_company_map(db: Session, user_ids: set[int]) -> dict[int, Any]:
    if not user_ids:
        return {}
    rows = db.execute(select(User.id, User.company_id).where(User.id.in_(user_ids)))
    return {int(r[0]): r[1] for r in rows}


async def _ensure_account_access(
    account_id: int,
    current_user: User,
    db: Session,
    storage,
) -> dict[str, Any]:
    acc = storage.get_account(account_id)
    if not acc:
        logger.warning(
            "wallet access denied: account missing; account_id=%s user_id=%s company_id=%s",
            account_id,
            getattr(current_user, "id", None),
            getattr(current_user, "company_id", None),
        )
        raise HTTPException(status_code=404, detail="account not found")
    await _ensure_user_in_company(int(acc.get("user_id", 0)), current_user, db)
    return acc


async def _filter_accounts_for_user(
    items: list[dict[str, Any]], current_user: User, db: Session
) -> list[dict[str, Any]]:
    if _is_platform_admin(current_user):
        return items
    user_ids = {int(i.get("user_id")) for i in items if i.get("user_id") is not None}
    company_map = await _load_company_map(db, user_ids)
    current_company = getattr(current_user, "company_id", None)
    return [i for i in items if company_map.get(int(i.get("user_id", 0))) == current_company]


# =============================================================================
# Pydantic схемы (самодостаточно, без внешних импортов)
# =============================================================================
CurrencyStr = constr(strip_whitespace=True, min_length=3, max_length=10)  # type: ignore


class WalletAccountCreate(BaseModel):
    user_id: conint(ge=1)  # type: ignore
    currency: CurrencyStr
    balance: Decimal | None = Field(None, description="Начальный баланс (опционально)")


class WalletAccountOut(BaseModel):
    id: conint(ge=1)  # type: ignore
    user_id: conint(ge=1)  # type: ignore
    currency: CurrencyStr
    balance: str
    created_at: str
    updated_at: str


class WalletDeposit(BaseModel):
    amount: Decimal = Field(..., gt=0)
    reference: str | None = Field(None, max_length=255)


class WalletWithdraw(BaseModel):
    amount: Decimal = Field(..., gt=0)
    reference: str | None = Field(None, max_length=255)


class WalletTransfer(BaseModel):
    source_account_id: conint(ge=1)  # type: ignore
    destination_account_id: conint(ge=1)  # type: ignore
    amount: Decimal = Field(..., gt=0)
    reference: str | None = Field(None, max_length=255)


class WalletTxBalance(BaseModel):
    account_id: conint(ge=1)  # type: ignore
    currency: CurrencyStr
    balance: str


class WalletTransferOut(BaseModel):
    source: WalletTxBalance
    destination: WalletTxBalance


class WalletTransactionOut(BaseModel):
    """
    Универсальная схема ответа для депозит/списание: баланс и валюта.
    Для transfer используем WalletTransferOut.
    """

    account_id: conint(ge=1)  # type: ignore
    currency: CurrencyStr
    balance: str


class BalanceOut(BaseModel):
    account_id: conint(ge=1)  # type: ignore
    currency: CurrencyStr
    balance: str


class PageMeta(BaseModel):
    page: conint(ge=1)  # type: ignore
    size: conint(ge=1, le=200)  # type: ignore
    total: int


class LedgerItem(BaseModel):
    id: conint(ge=1)  # type: ignore
    account_id: conint(ge=1)  # type: ignore
    type: str
    amount: str
    currency: CurrencyStr
    reference: str | None
    created_at: str


class LedgerPage(BaseModel):
    items: list[LedgerItem]
    meta: PageMeta


class WalletAccountsPage(BaseModel):
    items: list[WalletAccountOut]
    meta: PageMeta


class HealthOut(BaseModel):
    ok: bool
    engine: str | None = None
    error: str | None = None


class StatsOut(BaseModel):
    accounts: int
    ledger_entries: int
    total_balance: str


# =============================================================================
# Storage backend (SQL реализация) — ленивое получение
# =============================================================================
_BACKEND = "sql"


def _get_storage(db: Session):
    try:
        from app.storage.wallet_sql import WalletStorageSQL  # type: ignore

        return WalletStorageSQL(db)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("wallet storage init failed: %s", e)
        raise HTTPException(status_code=500, detail="wallet storage unavailable")


def _storage_caps(storage) -> dict[str, bool]:
    s = storage
    return {
        "has_get_uc": hasattr(s, "get_account_by_user_currency"),
        "has_list_ext": hasattr(s, "list_accounts") and "currency" in inspect.signature(s.list_accounts).parameters,  # type: ignore[arg-type]
        "has_adjust": hasattr(s, "adjust_balance"),
        "has_health": hasattr(s, "health"),
        "has_stats": hasattr(s, "stats"),
    }


# =============================================================================
# Router
# =============================================================================
router = APIRouter(
    prefix="/api/v1/wallet",
    tags=["wallet"],
    responses={
        400: {"description": "Bad Request"},
        401: {"description": "Unauthorized"},
        403: {"description": "Forbidden"},
        404: {"description": "Not Found"},
        409: {"description": "Conflict"},
        500: {"description": "Internal Server Error"},
    },
)


# =============================================================================
# HEALTH / STATS
# =============================================================================
@router.get("/health", response_model=HealthOut, summary="Здоровье storage-слоя")
def health(db: Session = Depends(get_db)) -> HealthOut:
    storage = _get_storage(db)
    caps = _storage_caps(storage)
    if caps.get("has_health"):
        try:
            data = storage.health()  # type: ignore[attr-defined]
            return HealthOut(**data) if isinstance(data, dict) else HealthOut(ok=_safe_bool(data))
        except HTTPException:
            raise
        except Exception as e:
            logger.exception("wallet.health failed: %s", e)
            return HealthOut(ok=False, error="wallet storage unavailable")
    # Базовый ответ, если метод отсутствует
    return HealthOut(ok=True, engine=_BACKEND)


@router.get("/stats", response_model=StatsOut, summary="Агрегированная статистика")
def stats(db: Session = Depends(get_db)) -> StatsOut:
    storage = _get_storage(db)
    caps = _storage_caps(storage)
    if caps.get("has_stats"):
        try:
            data = storage.stats()  # type: ignore[attr-defined]
            # ожидание: {"accounts": int, "ledger_entries": int, "total_balance": str|Decimal}
            if isinstance(data, dict):
                tb = _to_dec_str(data.get("total_balance", "0"))
                return StatsOut(
                    accounts=int(data.get("accounts", 0)),
                    ledger_entries=int(data.get("ledger_entries", 0)),
                    total_balance=tb,
                )
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=_pick_http_status(e), detail="wallet storage unavailable")
    # Fallback — без агрегации из БД
    try:
        rows = storage.list_accounts()  # type: ignore[call-arg]
        items = rows["items"] if isinstance(rows, dict) and "items" in rows else rows
        acc_count = len(items) if isinstance(items, list) else 0
        return StatsOut(accounts=acc_count, ledger_entries=0, total_balance="0")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=_pick_http_status(e), detail=str(e))


# =============================================================================
# ACCOUNTS
# =============================================================================
@router.post(
    "/accounts",
    response_model=WalletAccountOut,
    status_code=status.HTTP_201_CREATED,
    summary="Создать кошелёк (идемпотентно по user_id+currency)",
)
async def create_account(
    req: WalletAccountCreate,
    x_request_id: str | None = Header(default=None, alias="X-Request-Id"),
    current_user: User = Depends(require_role("admin", "manager")),
    db: Session = Depends(get_db),
) -> WalletAccountOut:
    try:
        await _ensure_user_in_company(req.user_id, current_user, db)
        ccy = _norm_ccy(req.currency)
        storage = _get_storage(db)
        acc = storage.create_account(req.user_id, ccy, initial_balance=req.balance)  # type: ignore[call-arg]
        acc["currency"] = _norm_ccy(acc.get("currency", ccy))
        acc["balance"] = _to_dec_str(acc.get("balance", "0"))
        return WalletAccountOut(**acc)
    except HTTPException:
        raise
    except Exception as e:
        logger.warning("create_account failed; rid=%s; err=%s", x_request_id, e)
        raise HTTPException(status_code=_pick_http_status(e), detail=str(e))


@router.get(
    "/accounts",
    response_model=WalletAccountsPage,
    summary="Список кошельков (фильтр по user_id/currency, пагинация)",
)
async def list_accounts(
    user_id: int | None = Query(None, ge=1),
    currency: str | None = Query(None, min_length=3, max_length=10),
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=200),
    current_user: User = Depends(_auth_user),
    db: Session = Depends(get_db),
) -> WalletAccountsPage:
    try:
        ccy = _norm_ccy(currency) if currency else None
        if user_id is not None:
            await _ensure_user_in_company(user_id, current_user, db)

        allowed_ids: list[int] | None = None
        if not _is_platform_admin(current_user):
            rows = db.execute(select(User.id).where(User.company_id == getattr(current_user, "company_id", None)))
            allowed_ids = [int(r[0]) for r in rows]
            if user_id is not None:
                allowed_ids = [uid for uid in allowed_ids if uid == user_id]
            if allowed_ids is not None and not allowed_ids:
                allowed_ids = [-1]

        storage = _get_storage(db)
        caps = _storage_caps(storage)

        if caps.get("has_list_ext"):
            rows = storage.list_accounts(user_id=user_id, currency=ccy, page=page, size=size, user_ids=allowed_ids)  # type: ignore[attr-defined]
        else:
            rows = storage.list_accounts(user_id=user_id, user_ids=allowed_ids)  # type: ignore[call-arg]
            items = rows["items"] if isinstance(rows, dict) and "items" in rows else rows
            if not isinstance(items, list):
                items = []
            if ccy:
                items = [r for r in items if _norm_ccy(r.get("currency", "")) == ccy]
            rows = {"items": items, "meta": {"page": page, "size": size, "total": len(items)}}

        items = rows["items"] if isinstance(rows, dict) and "items" in rows else rows
        if not isinstance(items, list):
            items = []
        filtered = await _filter_accounts_for_user(items, current_user, db)
        if ccy:
            filtered = [r for r in filtered if _norm_ccy(r.get("currency", "")) == ccy]
        total = len(filtered)
        start = (page - 1) * size
        end = start + size
        page_items = filtered[start:end]
        for r in page_items:
            r["currency"] = _norm_ccy(r.get("currency", ""))
            r["balance"] = _to_dec_str(r.get("balance", "0"))

        meta = {"page": page, "size": size, "total": total}
        return WalletAccountsPage(items=[WalletAccountOut(**r) for r in page_items], meta=PageMeta(**meta))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=_pick_http_status(e), detail=str(e))


@router.get(
    "/accounts/{account_id}",
    response_model=WalletAccountOut,
    summary="Получить кошелёк по ID",
)
async def get_account(
    account_id: int = Path(..., ge=1),
    current_user: User = Depends(_auth_user),
    db: Session = Depends(get_db),
) -> WalletAccountOut:
    try:
        storage = _get_storage(db)
        acc = await _ensure_account_access(account_id, current_user, db, storage)
        acc["currency"] = _norm_ccy(acc.get("currency", ""))
        acc["balance"] = _to_dec_str(acc.get("balance", "0"))
        return WalletAccountOut(**acc)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=_pick_http_status(e), detail=str(e))


@router.get(
    "/accounts/by-user",
    response_model=WalletAccountOut,
    summary="Получить кошелёк по user_id и валюте",
)
async def get_account_by_user_currency(
    user_id: int = Query(..., ge=1),
    currency: str = Query(..., min_length=3, max_length=10),
    current_user: User = Depends(_auth_user),
    db: Session = Depends(get_db),
) -> WalletAccountOut:
    await _ensure_user_in_company(user_id, current_user, db)
    storage = _get_storage(db)
    caps = _storage_caps(storage)

    if not caps.get("has_get_uc"):
        rows = storage.list_accounts(user_id=user_id)  # type: ignore[call-arg]
        items = rows["items"] if isinstance(rows, dict) and "items" in rows else rows
        if not isinstance(items, list):
            items = []
        ccy = _norm_ccy(currency)
        for r in items:
            if _norm_ccy(r.get("currency", "")) == ccy:
                r["currency"] = _norm_ccy(r.get("currency", ""))
                r["balance"] = _to_dec_str(r.get("balance", "0"))
                await _ensure_user_in_company(int(r.get("user_id", 0)), current_user, db)
                return WalletAccountOut(**r)
        raise HTTPException(status_code=404, detail="account not found")
    try:
        acc = storage.get_account_by_user_currency(user_id=user_id, currency=_norm_ccy(currency))  # type: ignore[attr-defined]
        if not acc:
            raise HTTPException(status_code=404, detail="account not found")
        await _ensure_user_in_company(int(acc.get("user_id", 0)), current_user, db)
        acc["currency"] = _norm_ccy(acc.get("currency", ""))
        acc["balance"] = _to_dec_str(acc.get("balance", "0"))
        return WalletAccountOut(**acc)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=_pick_http_status(e), detail=str(e))


@router.get(
    "/accounts/{account_id}/balance",
    response_model=BalanceOut,
    summary="Баланс кошелька",
)
async def get_balance(
    account_id: int = Path(..., ge=1),
    current_user: User = Depends(_auth_user),
    db: Session = Depends(get_db),
) -> BalanceOut:
    try:
        storage = _get_storage(db)
        await _ensure_account_access(account_id, current_user, db, storage)
        bal = storage.get_balance(account_id)
        if isinstance(bal, dict):
            return BalanceOut(
                account_id=int(bal.get("account_id", account_id)),
                currency=_norm_ccy(bal.get("currency", "")),
                balance=_to_dec_str(bal.get("balance", "0")),
            )
        acc = storage.get_account(account_id)
        ccy = _norm_ccy(acc["currency"]) if acc and "currency" in acc else ""
        return BalanceOut(account_id=account_id, currency=ccy, balance=_to_dec_str(bal))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=_pick_http_status(e), detail=str(e))


# =============================================================================
# MONEY OPS
# =============================================================================
@router.post(
    "/accounts/{account_id}/deposit",
    response_model=WalletTransactionOut,
    summary="Пополнение счёта",
)
async def deposit(
    account_id: int = Path(..., ge=1),
    req: WalletDeposit = ...,
    x_request_id: str | None = Header(default=None, alias="X-Request-Id"),
    current_user: User = Depends(require_role("admin", "manager")),
    db: Session = Depends(get_db),
) -> WalletTransactionOut:
    try:
        storage = _get_storage(db)
        await _ensure_account_access(account_id, current_user, db, storage)
        out = storage.deposit(account_id, req.amount, getattr(req, "reference", None))
        return WalletTransactionOut(
            account_id=int(out.get("account_id", account_id)),
            currency=_norm_ccy(out.get("currency", "")),
            balance=_to_dec_str(out.get("balance", "0")),
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.warning("deposit failed (id=%s rid=%s): %s", account_id, x_request_id, e)
        raise HTTPException(status_code=_pick_http_status(e), detail=str(e))


@router.post(
    "/accounts/{account_id}/withdraw",
    response_model=WalletTransactionOut,
    summary="Списание со счёта",
)
async def withdraw(
    account_id: int = Path(..., ge=1),
    req: WalletWithdraw = ...,
    x_request_id: str | None = Header(default=None, alias="X-Request-Id"),
    current_user: User = Depends(require_role("admin", "manager")),
    db: Session = Depends(get_db),
) -> WalletTransactionOut:
    try:
        storage = _get_storage(db)
        await _ensure_account_access(account_id, current_user, db, storage)
        out = storage.withdraw(account_id, req.amount, getattr(req, "reference", None))
        return WalletTransactionOut(
            account_id=int(out.get("account_id", account_id)),
            currency=_norm_ccy(out.get("currency", "")),
            balance=_to_dec_str(out.get("balance", "0")),
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.warning("withdraw failed (id=%s rid=%s): %s", account_id, x_request_id, e)
        raise HTTPException(status_code=_pick_http_status(e), detail=str(e))


@router.post(
    "/transfer",
    response_model=WalletTransferOut,
    summary="Перевод между кошельками (одна валюта)",
)
async def transfer(
    req: WalletTransfer,
    x_request_id: str | None = Header(default=None, alias="X-Request-Id"),
    current_user: User = Depends(require_role("admin", "manager")),
    db: Session = Depends(get_db),
) -> WalletTransferOut:
    if req.source_account_id == req.destination_account_id:
        raise HTTPException(status_code=400, detail="source and destination must differ")
    try:
        storage = _get_storage(db)
        src_acc = await _ensure_account_access(req.source_account_id, current_user, db, storage)
        dst_acc = await _ensure_account_access(req.destination_account_id, current_user, db, storage)
        if not _is_platform_admin(current_user):
            src_company = getattr(
                await _ensure_user_in_company(int(src_acc.get("user_id", 0)), current_user, db), "company_id", None
            )
            dst_company = getattr(
                await _ensure_user_in_company(int(dst_acc.get("user_id", 0)), current_user, db), "company_id", None
            )
            if src_company != dst_company:
                raise HTTPException(status_code=404, detail="account not found")

        out = storage.transfer(
            req.source_account_id,
            req.destination_account_id,
            req.amount,
            getattr(req, "reference", None),
        )
        src = out.get("source", {}) if isinstance(out, dict) else {}
        dst = out.get("destination", {}) if isinstance(out, dict) else {}
        source = WalletTxBalance(
            account_id=int(src.get("account_id", req.source_account_id)),
            currency=_norm_ccy(src.get("currency", "")),
            balance=_to_dec_str(src.get("balance", "0")),
        )
        destination = WalletTxBalance(
            account_id=int(dst.get("account_id", req.destination_account_id)),
            currency=_norm_ccy(dst.get("currency", "")),
            balance=_to_dec_str(dst.get("balance", "0")),
        )
        return WalletTransferOut(source=source, destination=destination)
    except HTTPException:
        raise
    except Exception as e:
        logger.warning(
            "transfer failed %s->%s; rid=%s; err=%s",
            req.source_account_id,
            req.destination_account_id,
            x_request_id,
            e,
        )
        raise HTTPException(status_code=_pick_http_status(e), detail=str(e))


# =============================================================================
# LEDGER
# =============================================================================
@router.get(
    "/accounts/{account_id}/ledger",
    response_model=LedgerPage,
    summary="Лента операций по счёту",
)
async def ledger(
    account_id: int = Path(..., ge=1),
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=200),
    current_user: User = Depends(_auth_user),
    db: Session = Depends(get_db),
) -> LedgerPage:
    try:
        storage = _get_storage(db)
        await _ensure_account_access(account_id, current_user, db, storage)
        page_obj = storage.list_ledger(account_id, page, size)
        items = page_obj.get("items", []) if isinstance(page_obj, dict) else []
        meta = (
            page_obj.get("meta", {"page": page, "size": size, "total": len(items)})
            if isinstance(page_obj, dict)
            else {"page": page, "size": size, "total": len(items)}
        )
        norm_items: list[LedgerItem] = []
        for it in items:
            norm_items.append(
                LedgerItem(
                    id=int(it.get("id")),
                    account_id=int(it.get("account_id", account_id)),
                    type=str(it.get("type", it.get("entry_type", ""))),
                    amount=_to_dec_str(it.get("amount", "0")),
                    currency=_norm_ccy(it.get("currency", "")),
                    reference=it.get("reference"),
                    created_at=str(it.get("created_at", "")),
                )
            )
        return LedgerPage(items=norm_items, meta=PageMeta(**meta))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=_pick_http_status(e), detail=str(e))


# =============================================================================
# ADMIN: ADJUST BALANCE (если поддерживается стореджем)
# =============================================================================
class AdjustIn(BaseModel):
    new_balance: Decimal
    reference: str | None = Field(None, max_length=255)


@router.post(
    "/accounts/{account_id}/adjust",
    response_model=WalletTransactionOut,
    summary="Коррекция баланса (admin, если поддерживается хранилищем)",
)
async def adjust_balance(
    account_id: int = Path(..., ge=1),
    payload: AdjustIn = ...,
    current_user: User = Depends(require_role("admin")),
    db: Session = Depends(get_db),
):
    storage = _get_storage(db)
    caps = _storage_caps(storage)
    if not caps.get("has_adjust"):
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="adjust_balance not supported by storage",
        )
    try:
        await _ensure_account_access(account_id, current_user, db, storage)
        out = storage.adjust_balance(account_id, payload.new_balance, payload.reference)  # type: ignore[attr-defined]
        return WalletTransactionOut(
            account_id=int(out.get("account_id", account_id)),
            currency=_norm_ccy(out.get("currency", "")),
            balance=_to_dec_str(out.get("balance", "0")),
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=_pick_http_status(e), detail=str(e))
