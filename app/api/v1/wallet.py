# app/api/v1/wallet.py
from __future__ import annotations

import inspect
import logging
from decimal import Decimal
from typing import Any, TypeVar

from fastapi import APIRouter, Depends, Header, HTTPException, Path, Query, status
from pydantic import BaseModel, Field, conint, constr, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_async_db
from app.core.dependencies import (
    get_current_verified_user,
    require_active_subscription,
    require_company_access,
    require_store_admin_company,
)
from app.core.exceptions import AuthorizationError, NotFoundError
from app.core.money import MoneyNormalizationError, format_money, is_kzt, normalize_money
from app.core.rbac import is_platform_admin, is_store_admin, is_store_manager
from app.core.security import resolve_tenant_company_id
from app.models.user import User
from app.services.wallet_api_helpers import (
    adjust_balance_flow,
    create_account_flow,
    deposit_flow,
    get_account_by_user_currency_flow,
    get_account_flow,
    get_balance_flow,
    ledger_flow,
    list_accounts_flow,
    stats_read_flow,
    transfer_flow,
    withdraw_flow,
)
from app.storage.wallet_sql import WalletStorageSQL

# =============================================================================
# ЛОГИРОВАНИЕ
# =============================================================================
logger = logging.getLogger(__name__)


T = TypeVar("T")


async def _auth_user(current_user: User = Depends(get_current_verified_user)) -> User:
    return current_user


async def _require_company_context(current_user: User = Depends(get_current_verified_user)) -> User:
    if is_platform_admin(current_user):
        resolve_tenant_company_id(current_user, not_found_detail="Company not set")
        return current_user
    resolve_tenant_company_id(current_user, not_found_detail="Company not set")
    return current_user


# =============================================================================
# ХЕЛПЕРЫ
# =============================================================================
def _norm_ccy(code: str | None) -> str:
    v = (code or "").strip().upper()
    if not (3 <= len(v) <= 10):
        raise HTTPException(status_code=400, detail="invalid_currency")
    return v


_NON_KZT_PLACES = 6


def _to_dec_str(v: Any, currency: str) -> str:
    try:
        return format_money(v, currency, non_kzt_places=_NON_KZT_PLACES)
    except MoneyNormalizationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _require_kzt_integer_amount(amount: Decimal, currency: str) -> Decimal:
    if not is_kzt(currency):
        return amount
    try:
        return normalize_money(amount, currency, non_kzt_places=0)
    except MoneyNormalizationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


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


async def _ensure_user_in_company(
    target_user_id: int,
    current_user: User,
    db: AsyncSession,
    *,
    not_found_detail: str = "account not found",
) -> User:
    resolved_company_id = resolve_tenant_company_id(current_user, not_found_detail="Company not set")
    user = await db.get(User, target_user_id)
    if not user:
        raise HTTPException(status_code=404, detail=not_found_detail)
    if getattr(user, "company_id", None) != resolved_company_id:
        raise HTTPException(status_code=404, detail=not_found_detail)
    return user


def _is_privileged_wallet_user(user: User) -> bool:
    if is_store_admin(user) or is_store_manager(user):
        return True
    return False


def _ensure_platform_admin_self_scope(current_user: User, target_user_id: int) -> None:
    """Wallet policy: platform_admin only self-scope on public wallet endpoints."""
    if is_platform_admin(current_user) and target_user_id != int(getattr(current_user, "id", 0) or 0):
        raise AuthorizationError("Insufficient permissions", "FORBIDDEN")


async def _load_company_map(db: AsyncSession, user_ids: set[int]) -> dict[int, Any]:
    if not user_ids:
        return {}
    stmt = select(User.id, User.company_id).where(User.id.in_(user_ids))
    rows = (await db.execute(stmt)).all()
    return {int(r[0]): r[1] for r in rows}


async def _ensure_account_access(
    account_id: int,
    current_user: User,
    db: AsyncSession,
) -> dict[str, Any]:
    resolved_company_id = resolve_tenant_company_id(current_user, not_found_detail="Company not set")
    storage = await _get_storage(db)
    acc = await storage.get_account(account_id, company_id=resolved_company_id)
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
    items: list[dict[str, Any]], current_user: User, db: AsyncSession
) -> list[dict[str, Any]]:
    resolved_company_id = resolve_tenant_company_id(current_user, not_found_detail="Company not set")
    filtered: list[dict[str, Any]] = []
    for i in items:
        uid = i.get("user_id")
        if uid is None:
            continue
        user = await db.get(User, int(uid))
        if user and getattr(user, "company_id", None) == resolved_company_id:
            filtered.append(i)
    return filtered


# =============================================================================
# Pydantic схемы (самодостаточно, без внешних импортов)
# =============================================================================
CurrencyStr = constr(strip_whitespace=True, min_length=3, max_length=10)  # type: ignore


class WalletAccountCreate(BaseModel):
    user_id: conint(ge=1)  # type: ignore
    currency: CurrencyStr
    balance: Decimal | None = Field(None, description="Начальный баланс (опционально)")

    @model_validator(mode="after")
    def _validate_kzt_balance(self) -> WalletAccountCreate:
        if self.balance is not None:
            _require_kzt_integer_amount(self.balance, self.currency)
        return self


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


def _storage_caps(storage) -> dict[str, bool]:
    s = storage
    return {
        "has_get_uc": hasattr(s, "get_account_by_user_currency"),
        "has_list_ext": hasattr(s, "list_accounts") and "currency" in inspect.signature(s.list_accounts).parameters,  # type: ignore[arg-type]
        "has_adjust": hasattr(s, "adjust_balance"),
        "has_health": hasattr(s, "health"),
        "has_stats": hasattr(s, "stats"),
    }


async def _get_storage(db: AsyncSession) -> WalletStorageSQL:
    return WalletStorageSQL(db)


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
read_router = APIRouter(
    dependencies=[
        Depends(require_company_access),
        Depends(_require_company_context),
        Depends(require_active_subscription),
    ],
)
admin_router = APIRouter(
    dependencies=[
        Depends(require_company_access),
        Depends(_require_company_context),
        Depends(require_store_admin_company),
        Depends(require_active_subscription),
    ],
)


# =============================================================================
# HEALTH / STATS
# =============================================================================
@router.get("/health", response_model=HealthOut, summary="Здоровье storage-слоя")
async def health(db: AsyncSession = Depends(get_async_db)) -> HealthOut:
    storage = await _get_storage(db)
    caps = _storage_caps(storage)
    if caps.get("has_health"):
        try:
            data = await storage.health()  # type: ignore[attr-defined]
            return HealthOut(**data) if isinstance(data, dict) else HealthOut(ok=_safe_bool(data))
        except HTTPException:
            raise
        except Exception as e:  # noqa: PERF203 — need detail for ops
            logger.exception("wallet.health failed: %s", e)
            return HealthOut(ok=False, error="wallet storage unavailable")
    return HealthOut(ok=True, engine=_BACKEND)


@read_router.get("/stats", response_model=StatsOut, summary="Агрегированная статистика")
async def stats(db: AsyncSession = Depends(get_async_db)) -> StatsOut:
    data = await stats_read_flow(
        db=db,
        get_storage_fn=_get_storage,
        storage_caps_fn=_storage_caps,
        to_dec_str_fn=_to_dec_str,
    )
    return StatsOut(**data)


# =============================================================================
# ACCOUNTS
# =============================================================================
@admin_router.post(
    "/accounts",
    response_model=WalletAccountOut,
    status_code=status.HTTP_201_CREATED,
    summary="Создать кошелёк (идемпотентно по user_id+currency)",
)
async def create_account(
    req: WalletAccountCreate,
    x_request_id: str | None = Header(default=None, alias="X-Request-Id"),
    current_user: User = Depends(get_current_verified_user),
    db: AsyncSession = Depends(get_async_db),
) -> WalletAccountOut:
    try:
        acc = await create_account_flow(
            req=req,
            current_user=current_user,
            db=db,
            ensure_platform_admin_self_scope_fn=_ensure_platform_admin_self_scope,
            ensure_user_in_company_fn=_ensure_user_in_company,
            norm_ccy_fn=_norm_ccy,
            to_dec_str_fn=_to_dec_str,
            get_storage_fn=_get_storage,
        )
        await db.commit()
        return WalletAccountOut(**acc)
    except AuthorizationError:
        raise
    except HTTPException:
        raise
    except Exception as e:
        logger.warning("create_account failed; rid=%s; err=%s", x_request_id, e)
        await db.rollback()
        raise HTTPException(status_code=_pick_http_status(e), detail=str(e))


@read_router.get(
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
    db: AsyncSession = Depends(get_async_db),
) -> WalletAccountsPage:
    try:
        rows = await list_accounts_flow(
            user_id=user_id,
            currency=currency,
            page=page,
            size=size,
            current_user=current_user,
            db=db,
            ensure_user_in_company_fn=_ensure_user_in_company,
            norm_ccy_fn=_norm_ccy,
            get_storage_fn=_get_storage,
            storage_caps_fn=_storage_caps,
            filter_accounts_for_user_fn=_filter_accounts_for_user,
            to_dec_str_fn=_to_dec_str,
        )
        items = rows.get("items", [])
        meta = rows.get("meta", {"page": page, "size": size, "total": len(items)})
        return WalletAccountsPage(items=[WalletAccountOut(**r) for r in items], meta=PageMeta(**meta))
    except AuthorizationError:
        raise
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=_pick_http_status(e), detail=str(e))


@read_router.get(
    "/accounts/by-user",
    response_model=WalletAccountOut,
    summary="Получить кошелёк по user_id и валюте",
)
async def get_account_by_user_currency(
    user_id: int = Query(..., ge=1),
    currency: str = Query(..., min_length=3, max_length=10),
    current_user: User = Depends(_auth_user),
    db: AsyncSession = Depends(get_async_db),
) -> WalletAccountOut:
    try:
        acc = await get_account_by_user_currency_flow(
            user_id=user_id,
            currency=currency,
            current_user=current_user,
            db=db,
            is_privileged_wallet_user_fn=_is_privileged_wallet_user,
            ensure_user_in_company_fn=_ensure_user_in_company,
            get_storage_fn=_get_storage,
            storage_caps_fn=_storage_caps,
            norm_ccy_fn=_norm_ccy,
            to_dec_str_fn=_to_dec_str,
        )
        return WalletAccountOut(**acc)
    except AuthorizationError:
        raise
    except HTTPException:
        raise
    except NotFoundError:
        raise
    except Exception as e:
        raise HTTPException(status_code=_pick_http_status(e), detail=str(e))


@read_router.get(
    "/accounts/{account_id}",
    response_model=WalletAccountOut,
    summary="Получить кошелёк по ID",
)
async def get_account(
    account_id: int = Path(..., ge=1),
    current_user: User = Depends(_auth_user),
    db: AsyncSession = Depends(get_async_db),
) -> WalletAccountOut:
    try:
        acc = await get_account_flow(
            account_id=account_id,
            current_user=current_user,
            db=db,
            ensure_account_access_fn=_ensure_account_access,
            norm_ccy_fn=_norm_ccy,
            to_dec_str_fn=_to_dec_str,
        )
        return WalletAccountOut(**acc)
    except AuthorizationError:
        raise
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=_pick_http_status(e), detail=str(e))


@read_router.get(
    "/accounts/{account_id}/balance",
    response_model=BalanceOut,
    summary="Баланс кошелька",
)
async def get_balance(
    account_id: int = Path(..., ge=1),
    current_user: User = Depends(_auth_user),
    db: AsyncSession = Depends(get_async_db),
) -> BalanceOut:
    try:
        data = await get_balance_flow(
            account_id=account_id,
            current_user=current_user,
            db=db,
            ensure_account_access_fn=_ensure_account_access,
            get_storage_fn=_get_storage,
            norm_ccy_fn=_norm_ccy,
            to_dec_str_fn=_to_dec_str,
        )
        return BalanceOut(**data)
    except AuthorizationError:
        raise
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=_pick_http_status(e), detail=str(e))


# =============================================================================
# MONEY OPS
# =============================================================================
@admin_router.post(
    "/accounts/{account_id}/deposit",
    response_model=WalletTransactionOut,
    summary="Пополнение счёта",
)
async def deposit(
    account_id: int = Path(..., ge=1),
    req: WalletDeposit = ...,
    x_request_id: str | None = Header(default=None, alias="X-Request-Id"),
    current_user: User = Depends(get_current_verified_user),
    db: AsyncSession = Depends(get_async_db),
) -> WalletTransactionOut:
    try:
        out = await deposit_flow(
            account_id=account_id,
            req=req,
            x_request_id=x_request_id,
            current_user=current_user,
            db=db,
            ensure_account_access_fn=_ensure_account_access,
            get_storage_fn=_get_storage,
            require_kzt_integer_amount_fn=_require_kzt_integer_amount,
            norm_ccy_fn=_norm_ccy,
            to_dec_str_fn=_to_dec_str,
        )
        await db.commit()
        return WalletTransactionOut(**out)
    except AuthorizationError:
        raise
    except HTTPException:
        raise
    except Exception as e:
        logger.warning("deposit failed (id=%s rid=%s): %s", account_id, x_request_id, e)
        await db.rollback()
        raise HTTPException(status_code=_pick_http_status(e), detail=str(e))


@admin_router.post(
    "/accounts/{account_id}/withdraw",
    response_model=WalletTransactionOut,
    summary="Списание со счёта",
)
async def withdraw(
    account_id: int = Path(..., ge=1),
    req: WalletWithdraw = ...,
    x_request_id: str | None = Header(default=None, alias="X-Request-Id"),
    current_user: User = Depends(get_current_verified_user),
    db: AsyncSession = Depends(get_async_db),
) -> WalletTransactionOut:
    try:
        out = await withdraw_flow(
            account_id=account_id,
            req=req,
            x_request_id=x_request_id,
            current_user=current_user,
            db=db,
            ensure_account_access_fn=_ensure_account_access,
            get_storage_fn=_get_storage,
            require_kzt_integer_amount_fn=_require_kzt_integer_amount,
            norm_ccy_fn=_norm_ccy,
            to_dec_str_fn=_to_dec_str,
        )
        await db.commit()
        return WalletTransactionOut(**out)
    except AuthorizationError:
        raise
    except HTTPException:
        raise
    except Exception as e:
        logger.warning("withdraw failed (id=%s rid=%s): %s", account_id, x_request_id, e)
        await db.rollback()
        raise HTTPException(status_code=_pick_http_status(e), detail=str(e))


@admin_router.post(
    "/transfer",
    response_model=WalletTransferOut,
    summary="Перевод между кошельками (одна валюта)",
)
async def transfer(
    req: WalletTransfer,
    x_request_id: str | None = Header(default=None, alias="X-Request-Id"),
    current_user: User = Depends(get_current_verified_user),
    db: AsyncSession = Depends(get_async_db),
) -> WalletTransferOut:
    try:
        out = await transfer_flow(
            req=req,
            x_request_id=x_request_id,
            current_user=current_user,
            db=db,
            ensure_account_access_fn=_ensure_account_access,
            ensure_user_in_company_fn=_ensure_user_in_company,
            get_storage_fn=_get_storage,
            require_kzt_integer_amount_fn=_require_kzt_integer_amount,
            norm_ccy_fn=_norm_ccy,
            to_dec_str_fn=_to_dec_str,
        )
        await db.commit()
        source = WalletTxBalance(**out["source"])
        destination = WalletTxBalance(**out["destination"])
        return WalletTransferOut(source=source, destination=destination)
    except AuthorizationError:
        raise
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
        await db.rollback()
        raise HTTPException(status_code=_pick_http_status(e), detail=str(e))


# =============================================================================
# LEDGER
# =============================================================================
@read_router.get(
    "/accounts/{account_id}/ledger",
    response_model=LedgerPage,
    summary="Лента операций по счёту",
)
async def ledger(
    account_id: int = Path(..., ge=1),
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=200),
    current_user: User = Depends(_auth_user),
    db: AsyncSession = Depends(get_async_db),
) -> LedgerPage:
    try:
        page_obj = await ledger_flow(
            account_id=account_id,
            page=page,
            size=size,
            current_user=current_user,
            db=db,
            ensure_account_access_fn=_ensure_account_access,
            get_storage_fn=_get_storage,
            norm_ccy_fn=_norm_ccy,
            to_dec_str_fn=_to_dec_str,
        )
        items = [LedgerItem(**it) for it in page_obj.get("items", [])]
        meta = PageMeta(**page_obj.get("meta", {"page": page, "size": size, "total": len(items)}))
        return LedgerPage(items=items, meta=meta)
    except AuthorizationError:
        raise
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


@admin_router.post(
    "/accounts/{account_id}/adjust",
    response_model=WalletTransactionOut,
    summary="Коррекция баланса (admin, если поддерживается хранилищем)",
)
async def adjust_balance(
    account_id: int = Path(..., ge=1),
    payload: AdjustIn = ...,
    x_request_id: str | None = Header(default=None, alias="X-Request-Id"),
    current_user: User = Depends(get_current_verified_user),
    db: AsyncSession = Depends(get_async_db),
):
    try:
        out = await adjust_balance_flow(
            account_id=account_id,
            payload=payload,
            x_request_id=x_request_id,
            current_user=current_user,
            db=db,
            ensure_account_access_fn=_ensure_account_access,
            get_storage_fn=_get_storage,
            storage_caps_fn=_storage_caps,
            norm_ccy_fn=_norm_ccy,
            to_dec_str_fn=_to_dec_str,
        )
        await db.commit()
        return WalletTransactionOut(**out)
    except AuthorizationError:
        raise
    except HTTPException:
        raise
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=_pick_http_status(e), detail=str(e))


router.include_router(read_router)
router.include_router(admin_router)
