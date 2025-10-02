# app/api/v1/wallet.py
from __future__ import annotations

import inspect
import logging
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional, Literal, Callable, Tuple

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    Path,
    status,
    Header,
)
from pydantic import BaseModel, Field, ConfigDict, conint, constr

# =============================================================================
# ЛОГИРОВАНИЕ
# =============================================================================
logger = logging.getLogger(__name__)

# =============================================================================
# DEMO RBAC / USER CTX (совместимо со стилем campaigns)
# =============================================================================
class UserCtx(BaseModel):
    id: int
    role: Literal["admin", "manager", "viewer"] = "manager"
    username: str = "demo"

async def get_current_user() -> UserCtx:
    # Демо-реализация. В проде подменяй на Depends(auth.get_current_user)
    return UserCtx(id=1, role="manager", username="demo")

def require_role(*roles: str):
    async def dep(user: UserCtx = Depends(get_current_user)):
        if user.role not in roles:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="forbidden")
        return user
    return dep

# =============================================================================
# ХЕЛПЕРЫ
# =============================================================================
def _norm_ccy(code: Optional[str]) -> str:
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

# =============================================================================
# Pydantic схемы (самодостаточно, без внешних импортов)
# =============================================================================
CurrencyStr = constr(strip_whitespace=True, min_length=3, max_length=10)  # type: ignore

class WalletAccountCreate(BaseModel):
    user_id: conint(ge=1)  # type: ignore
    currency: CurrencyStr
    balance: Optional[Decimal] = Field(None, description="Начальный баланс (опционально)")

class WalletAccountOut(BaseModel):
    id: conint(ge=1)  # type: ignore
    user_id: conint(ge=1)  # type: ignore
    currency: CurrencyStr
    balance: str
    created_at: str
    updated_at: str

class WalletDeposit(BaseModel):
    amount: Decimal = Field(..., gt=0)
    reference: Optional[str] = Field(None, max_length=255)

class WalletWithdraw(BaseModel):
    amount: Decimal = Field(..., gt=0)
    reference: Optional[str] = Field(None, max_length=255)

class WalletTransfer(BaseModel):
    source_account_id: conint(ge=1)  # type: ignore
    destination_account_id: conint(ge=1)  # type: ignore
    amount: Decimal = Field(..., gt=0)
    reference: Optional[str] = Field(None, max_length=255)

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
    reference: Optional[str]
    created_at: str

class LedgerPage(BaseModel):
    items: List[LedgerItem]
    meta: PageMeta

class WalletAccountsPage(BaseModel):
    items: List[WalletAccountOut]
    meta: PageMeta

class HealthOut(BaseModel):
    ok: bool
    engine: Optional[str] = None
    error: Optional[str] = None

class StatsOut(BaseModel):
    accounts: int
    ledger_entries: int
    total_balance: str

# =============================================================================
# Storage backend (SQL реализация)
# =============================================================================
try:
    from app.storage.wallet_sql import WalletStorageSQL  # type: ignore
    storage = WalletStorageSQL()
    _BACKEND = "sql"
except Exception as e:
    raise RuntimeError(f"Wallet storage init failed: {e}")

# ---- Возможные расширенные методы (работаем мягко, если их нет) -------------
_HAS_GET_ACC_BY_UC = hasattr(storage, "get_account_by_user_currency")
_HAS_LIST_ACC_EXT = "currency" in inspect.signature(storage.list_accounts).parameters if hasattr(storage, "list_accounts") else False
_HAS_ADJUST = hasattr(storage, "adjust_balance")
_HAS_HEALTH = hasattr(storage, "health")
_HAS_STATS = hasattr(storage, "stats")

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
def health() -> HealthOut:
    if _HAS_HEALTH:
        try:
            data = storage.health()  # type: ignore[attr-defined]
            return HealthOut(**data) if isinstance(data, dict) else HealthOut(ok=_safe_bool(data))
        except Exception as e:
            logger.exception("wallet.health failed: %s", e)
            return HealthOut(ok=False, error=str(e))
    # Базовый ответ, если метод отсутствует
    return HealthOut(ok=True, engine=_BACKEND)

@router.get("/stats", response_model=StatsOut, summary="Агрегированная статистика")
def stats() -> StatsOut:
    if _HAS_STATS:
        try:
            data = storage.stats()  # type: ignore[attr-defined]
            # ожидание: {"accounts": int, "ledger_entries": int, "total_balance": str|Decimal}
            if isinstance(data, dict):
                tb = _to_dec_str(data.get("total_balance", "0"))
                return StatsOut(accounts=int(data.get("accounts", 0)),
                                ledger_entries=int(data.get("ledger_entries", 0)),
                                total_balance=tb)
        except Exception as e:
            raise HTTPException(status_code=_pick_http_status(e), detail=str(e))
    # Fallback — без агрегации из БД
    try:
        rows = storage.list_accounts()  # type: ignore[call-arg]
        items = rows["items"] if isinstance(rows, dict) and "items" in rows else rows
        acc_count = len(items) if isinstance(items, list) else 0
        return StatsOut(accounts=acc_count, ledger_entries=0, total_balance="0")
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
    dependencies=[Depends(require_role("admin", "manager"))],
)
def create_account(
    req: WalletAccountCreate,
    x_request_id: Optional[str] = Header(default=None, alias="X-Request-Id"),
) -> WalletAccountOut:
    try:
        ccy = _norm_ccy(req.currency)
        acc = storage.create_account(req.user_id, ccy, initial_balance=req.balance)  # type: ignore[call-arg]
        # нормализуем
        acc["currency"] = _norm_ccy(acc.get("currency", ccy))
        acc["balance"] = _to_dec_str(acc.get("balance", "0"))
        return WalletAccountOut(**acc)
    except Exception as e:
        logger.warning("create_account failed; rid=%s; err=%s", x_request_id, e)
        raise HTTPException(status_code=_pick_http_status(e), detail=str(e))

@router.get(
    "/accounts",
    response_model=WalletAccountsPage,
    summary="Список кошельков (фильтр по user_id/currency, пагинация)",
)
def list_accounts(
    user_id: Optional[int] = Query(None, ge=1),
    currency: Optional[str] = Query(None, min_length=3, max_length=10),
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=200),
) -> WalletAccountsPage:
    try:
        ccy = _norm_ccy(currency) if currency else None

        # если сторедж поддерживает расширенные параметры — используем
        if _HAS_LIST_ACC_EXT:
            rows = storage.list_accounts(user_id=user_id, currency=ccy, page=page, size=size)  # type: ignore[attr-defined]
        else:
            rows = storage.list_accounts(user_id=user_id)  # type: ignore[call-arg]
            # локальная фильтрация и пагинация
            items = rows["items"] if isinstance(rows, dict) and "items" in rows else rows
            if not isinstance(items, list):
                items = []
            if ccy:
                items = [r for r in items if _norm_ccy(r.get("currency", "")) == ccy]
            total = len(items)
            start = (page - 1) * size
            end = start + size
            rows = {"items": items[start:end], "meta": {"page": page, "size": size, "total": total}}

        # нормализация валют/балансов
        items = rows["items"] if isinstance(rows, dict) and "items" in rows else rows
        if isinstance(items, list):
            for r in items:
                r["currency"] = _norm_ccy(r.get("currency", ""))
                r["balance"] = _to_dec_str(r.get("balance", "0"))

        meta = rows["meta"] if isinstance(rows, dict) and "meta" in rows else {"page": page, "size": size, "total": len(items) if isinstance(items, list) else 0}
        # валидация
        return WalletAccountsPage(
            items=[WalletAccountOut(**r) for r in (items or [])],
            meta=PageMeta(**meta),
        )
    except Exception as e:
        raise HTTPException(status_code=_pick_http_status(e), detail=str(e))

@router.get(
    "/accounts/{account_id}",
    response_model=WalletAccountOut,
    summary="Получить кошелёк по ID",
)
def get_account(
    account_id: int = Path(..., ge=1),
) -> WalletAccountOut:
    try:
        acc = storage.get_account(account_id)
        if not acc:
            raise HTTPException(status_code=404, detail="account not found")
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
def get_account_by_user_currency(
    user_id: int = Query(..., ge=1),
    currency: str = Query(..., min_length=3, max_length=10),
) -> WalletAccountOut:
    if not _HAS_GET_ACC_BY_UC:
        # локальный обход — пройдём по списку и найдём нужный
        rows = storage.list_accounts(user_id=user_id)  # type: ignore[call-arg]
        items = rows["items"] if isinstance(rows, dict) and "items" in rows else rows
        if not isinstance(items, list):
            items = []
        ccy = _norm_ccy(currency)
        for r in items:
            if _norm_ccy(r.get("currency", "")) == ccy:
                r["currency"] = _norm_ccy(r.get("currency", ""))
                r["balance"] = _to_dec_str(r.get("balance", "0"))
                return WalletAccountOut(**r)
        raise HTTPException(status_code=404, detail="account not found")
    try:
        acc = storage.get_account_by_user_currency(user_id=user_id, currency=_norm_ccy(currency))  # type: ignore[attr-defined]
        if not acc:
            raise HTTPException(status_code=404, detail="account not found")
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
def get_balance(
    account_id: int = Path(..., ge=1),
) -> BalanceOut:
    try:
        bal = storage.get_balance(account_id)
        if isinstance(bal, dict):
            return BalanceOut(
                account_id=int(bal.get("account_id", account_id)),
                currency=_norm_ccy(bal.get("currency", "")),
                balance=_to_dec_str(bal.get("balance", "0")),
            )
        # fallback: если вернули просто число
        acc = storage.get_account(account_id)
        ccy = _norm_ccy(acc["currency"]) if acc and "currency" in acc else ""
        return BalanceOut(account_id=account_id, currency=ccy, balance=_to_dec_str(bal))
    except Exception as e:
        raise HTTPException(status_code=_pick_http_status(e), detail=str(e))

# =============================================================================
# MONEY OPS
# =============================================================================
@router.post(
    "/accounts/{account_id}/deposit",
    response_model=WalletTransactionOut,
    summary="Пополнение счёта",
    dependencies=[Depends(require_role("admin", "manager"))],
)
def deposit(
    account_id: int = Path(..., ge=1),
    req: WalletDeposit = ...,
    x_request_id: Optional[str] = Header(default=None, alias="X-Request-Id"),
) -> WalletTransactionOut:
    try:
        out = storage.deposit(account_id, req.amount, getattr(req, "reference", None))
        # ожидаем out={"account_id":..,"currency":..,"balance":..}
        return WalletTransactionOut(
            account_id=int(out.get("account_id", account_id)),
            currency=_norm_ccy(out.get("currency", "")),
            balance=_to_dec_str(out.get("balance", "0")),
        )
    except Exception as e:
        logger.warning("deposit failed (id=%s rid=%s): %s", account_id, x_request_id, e)
        raise HTTPException(status_code=_pick_http_status(e), detail=str(e))

@router.post(
    "/accounts/{account_id}/withdraw",
    response_model=WalletTransactionOut,
    summary="Списание со счёта",
    dependencies=[Depends(require_role("admin", "manager"))],
)
def withdraw(
    account_id: int = Path(..., ge=1),
    req: WalletWithdraw = ...,
    x_request_id: Optional[str] = Header(default=None, alias="X-Request-Id"),
) -> WalletTransactionOut:
    try:
        out = storage.withdraw(account_id, req.amount, getattr(req, "reference", None))
        return WalletTransactionOut(
            account_id=int(out.get("account_id", account_id)),
            currency=_norm_ccy(out.get("currency", "")),
            balance=_to_dec_str(out.get("balance", "0")),
        )
    except Exception as e:
        logger.warning("withdraw failed (id=%s rid=%s): %s", account_id, x_request_id, e)
        raise HTTPException(status_code=_pick_http_status(e), detail=str(e))

@router.post(
    "/transfer",
    response_model=WalletTransferOut,
    summary="Перевод между кошельками (одна валюта)",
    dependencies=[Depends(require_role("admin", "manager"))],
)
def transfer(
    req: WalletTransfer,
    x_request_id: Optional[str] = Header(default=None, alias="X-Request-Id"),
) -> WalletTransferOut:
    if req.source_account_id == req.destination_account_id:
        raise HTTPException(status_code=400, detail="source and destination must differ")
    try:
        out = storage.transfer(
            req.source_account_id,
            req.destination_account_id,
            req.amount,
            getattr(req, "reference", None),
        )
        # ожидаем out={"source":{id,balance,currency}, "destination":{...}}
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
    except Exception as e:
        logger.warning(
            "transfer failed %s->%s; rid=%s; err=%s",
            req.source_account_id, req.destination_account_id, x_request_id, e
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
def ledger(
    account_id: int = Path(..., ge=1),
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=200),
) -> LedgerPage:
    try:
        page_obj = storage.list_ledger(account_id, page, size)
        # ожидаем {"items":[...], "meta":{"page":..,"size":..,"total":..}}
        items = page_obj.get("items", []) if isinstance(page_obj, dict) else []
        meta = page_obj.get("meta", {"page": page, "size": size, "total": len(items)}) if isinstance(page_obj, dict) else {"page": page, "size": size, "total": len(items)}
        # нормализация amounts/currency
        norm_items: List[LedgerItem] = []
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
    except Exception as e:
        raise HTTPException(status_code=_pick_http_status(e), detail=str(e))

# =============================================================================
# ADMIN: ADJUST BALANCE (если поддерживается стореджем)
# =============================================================================
class AdjustIn(BaseModel):
    new_balance: Decimal
    reference: Optional[str] = Field(None, max_length=255)

@router.post(
    "/accounts/{account_id}/adjust",
    response_model=WalletTransactionOut,
    summary="Коррекция баланса (admin, если поддерживается хранилищем)",
    dependencies=[Depends(require_role("admin"))],
)
def adjust_balance(
    account_id: int = Path(..., ge=1),
    payload: AdjustIn = ...,
):
    if not _HAS_ADJUST:
        raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail="adjust_balance not supported by storage")
    try:
        out = storage.adjust_balance(account_id, payload.new_balance, payload.reference)  # type: ignore[attr-defined]
        return WalletTransactionOut(
            account_id=int(out.get("account_id", account_id)),
            currency=_norm_ccy(out.get("currency", "")),
            balance=_to_dec_str(out.get("balance", "0")),
        )
    except Exception as e:
        raise HTTPException(status_code=_pick_http_status(e), detail=str(e))
