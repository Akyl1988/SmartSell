from __future__ import annotations

from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AuthorizationError, NotFoundError
from app.core.rbac import is_platform_admin
from app.core.security import resolve_tenant_company_id
from app.models.user import User


async def stats_read_flow(
    *,
    db: AsyncSession,
    get_storage_fn: Any,
    storage_caps_fn: Any,
    to_dec_str_fn: Any,
) -> dict[str, Any]:
    storage = await get_storage_fn(db)
    caps = storage_caps_fn(storage)
    if caps.get("has_stats"):
        data = await storage.stats()  # type: ignore[attr-defined]
        if isinstance(data, dict):
            tb = to_dec_str_fn(data.get("total_balance", "0"), data.get("currency", "KZT"))
            return {
                "accounts": int(data.get("accounts", 0)),
                "ledger_entries": int(data.get("ledger_entries", 0)),
                "total_balance": tb,
            }
    rows = await storage.list_accounts()  # type: ignore[call-arg]
    items = rows["items"] if isinstance(rows, dict) and "items" in rows else rows
    acc_count = len(items) if isinstance(items, list) else 0
    return {"accounts": acc_count, "ledger_entries": 0, "total_balance": "0"}


async def create_account_flow(
    *,
    req: Any,
    current_user: User,
    db: AsyncSession,
    ensure_platform_admin_self_scope_fn: Any,
    ensure_user_in_company_fn: Any,
    norm_ccy_fn: Any,
    to_dec_str_fn: Any,
    get_storage_fn: Any,
) -> dict[str, Any]:
    if is_platform_admin(current_user):
        ensure_platform_admin_self_scope_fn(current_user, req.user_id)
        if norm_ccy_fn(req.currency) != "KZT":
            raise AuthorizationError("Insufficient permissions", "FORBIDDEN")
    await ensure_user_in_company_fn(req.user_id, current_user, db)
    ccy = norm_ccy_fn(req.currency)
    storage = await get_storage_fn(db)
    acc = await storage.create_account(req.user_id, ccy, initial_balance=req.balance)
    acc["currency"] = norm_ccy_fn(acc.get("currency", ccy))
    acc["balance"] = to_dec_str_fn(acc.get("balance", "0"), acc.get("currency", ccy))
    return acc


async def list_accounts_flow(
    *,
    user_id: int | None,
    currency: str | None,
    page: int,
    size: int,
    current_user: User,
    db: AsyncSession,
    ensure_user_in_company_fn: Any,
    norm_ccy_fn: Any,
    get_storage_fn: Any,
    storage_caps_fn: Any,
    filter_accounts_for_user_fn: Any,
    to_dec_str_fn: Any,
) -> dict[str, Any]:
    if is_platform_admin(current_user):
        if user_id is None:
            raise AuthorizationError("Insufficient permissions", "FORBIDDEN")
        if user_id != int(getattr(current_user, "id", 0) or 0):
            raise AuthorizationError("Insufficient permissions", "FORBIDDEN")

    resolved_company_id = resolve_tenant_company_id(current_user, not_found_detail="Company not set")
    ccy = norm_ccy_fn(currency) if currency else None
    if user_id is not None:
        await ensure_user_in_company_fn(user_id, current_user, db)

    stmt = select(User.id).where(User.company_id == resolved_company_id)
    rows = (await db.execute(stmt)).all()
    allowed_ids: list[int] | None = [int(r[0]) for r in rows]
    if user_id is not None:
        allowed_ids = [uid for uid in allowed_ids if uid == user_id]
    if allowed_ids is not None and not allowed_ids:
        allowed_ids = [-1]

    storage = await get_storage_fn(db)
    caps = storage_caps_fn(storage)

    if caps.get("has_list_ext"):
        rows = await storage.list_accounts(
            user_id=user_id,
            currency=ccy,
            page=page,
            size=size,
            user_ids=allowed_ids,
            company_id=resolved_company_id,
        )
    else:
        rows = await storage.list_accounts(
            user_id=user_id,
            user_ids=allowed_ids,
            company_id=resolved_company_id,
        )
        items = rows["items"] if isinstance(rows, dict) and "items" in rows else rows
        if not isinstance(items, list):
            items = []
        if ccy:
            items = [r for r in items if norm_ccy_fn(r.get("currency", "")) == ccy]
        rows = {"items": items, "meta": {"page": page, "size": size, "total": len(items)}}

    items = rows["items"] if isinstance(rows, dict) and "items" in rows else rows
    if not isinstance(items, list):
        items = []
    filtered = await filter_accounts_for_user_fn(items, current_user, db)
    if ccy:
        filtered = [r for r in filtered if norm_ccy_fn(r.get("currency", "")) == ccy]
    total = len(filtered)
    start = (page - 1) * size
    end = start + size
    page_items = filtered[start:end]
    for r in page_items:
        r["currency"] = norm_ccy_fn(r.get("currency", ""))
        r["balance"] = to_dec_str_fn(r.get("balance", "0"), r.get("currency", ""))

    return {"items": page_items, "meta": {"page": page, "size": size, "total": total}}


async def get_account_by_user_currency_flow(
    *,
    user_id: int,
    currency: str,
    current_user: User,
    db: AsyncSession,
    is_privileged_wallet_user_fn: Any,
    ensure_user_in_company_fn: Any,
    get_storage_fn: Any,
    storage_caps_fn: Any,
    norm_ccy_fn: Any,
    to_dec_str_fn: Any,
) -> dict[str, Any]:
    if user_id != int(getattr(current_user, "id", 0) or 0) and not is_privileged_wallet_user_fn(current_user):
        raise AuthorizationError("Insufficient permissions", "FORBIDDEN")
    await ensure_user_in_company_fn(user_id, current_user, db)
    storage = await get_storage_fn(db)
    caps = storage_caps_fn(storage)

    if not caps.get("has_get_uc"):
        rows = await storage.list_accounts(user_id=user_id)
        items = rows["items"] if isinstance(rows, dict) and "items" in rows else rows
        if not isinstance(items, list):
            items = []
        ccy = norm_ccy_fn(currency)
        for r in items:
            if norm_ccy_fn(r.get("currency", "")) == ccy:
                r["currency"] = norm_ccy_fn(r.get("currency", ""))
                r["balance"] = to_dec_str_fn(r.get("balance", "0"), r.get("currency", ""))
                await ensure_user_in_company_fn(int(r.get("user_id", 0)), current_user, db)
                return r
        raise NotFoundError("wallet_account_not_found", code="WALLET_ACCOUNT_NOT_FOUND", http_status=404)

    acc = await storage.get_account_by_user_currency(user_id=user_id, currency=norm_ccy_fn(currency))
    if not acc:
        raise NotFoundError("wallet_account_not_found", code="WALLET_ACCOUNT_NOT_FOUND", http_status=404)
    await ensure_user_in_company_fn(int(acc.get("user_id", 0)), current_user, db)
    acc["currency"] = norm_ccy_fn(acc.get("currency", ""))
    acc["balance"] = to_dec_str_fn(acc.get("balance", "0"), acc.get("currency", ""))
    return acc


async def get_account_flow(
    *,
    account_id: int,
    current_user: User,
    db: AsyncSession,
    ensure_account_access_fn: Any,
    norm_ccy_fn: Any,
    to_dec_str_fn: Any,
) -> dict[str, Any]:
    if is_platform_admin(current_user):
        raise AuthorizationError("Insufficient permissions", "FORBIDDEN")
    acc = await ensure_account_access_fn(account_id, current_user, db)
    acc["currency"] = norm_ccy_fn(acc.get("currency", ""))
    acc["balance"] = to_dec_str_fn(acc.get("balance", "0"), acc.get("currency", ""))
    return acc


async def get_balance_flow(
    *,
    account_id: int,
    current_user: User,
    db: AsyncSession,
    ensure_account_access_fn: Any,
    get_storage_fn: Any,
    norm_ccy_fn: Any,
    to_dec_str_fn: Any,
) -> dict[str, Any]:
    if is_platform_admin(current_user):
        raise AuthorizationError("Insufficient permissions", "FORBIDDEN")
    resolved_company_id = resolve_tenant_company_id(current_user, not_found_detail="Company not set")
    await ensure_account_access_fn(account_id, current_user, db)
    storage = await get_storage_fn(db)
    bal = await storage.get_balance(account_id, company_id=resolved_company_id)
    if isinstance(bal, dict):
        return {
            "account_id": int(bal.get("account_id", account_id)),
            "currency": norm_ccy_fn(bal.get("currency", "")),
            "balance": to_dec_str_fn(bal.get("balance", "0"), bal.get("currency", "")),
        }
    acc = await storage.get_account(account_id)
    ccy = norm_ccy_fn(acc["currency"]) if acc and "currency" in acc else ""
    return {"account_id": account_id, "currency": ccy, "balance": to_dec_str_fn(bal, ccy)}


async def deposit_flow(
    *,
    account_id: int,
    req: Any,
    x_request_id: str | None,
    current_user: User,
    db: AsyncSession,
    ensure_account_access_fn: Any,
    get_storage_fn: Any,
    require_kzt_integer_amount_fn: Any,
    norm_ccy_fn: Any,
    to_dec_str_fn: Any,
) -> dict[str, Any]:
    if is_platform_admin(current_user):
        raise AuthorizationError("Insufficient permissions", "FORBIDDEN")
    resolved_company_id = resolve_tenant_company_id(current_user, not_found_detail="Company not set")
    await ensure_account_access_fn(account_id, current_user, db)
    storage = await get_storage_fn(db)
    acc = await storage.get_account(account_id, company_id=resolved_company_id)
    if acc:
        require_kzt_integer_amount_fn(req.amount, acc.get("currency", ""))
    out = await storage.deposit(
        account_id,
        req.amount,
        getattr(req, "reference", None),
        x_request_id,
        company_id=resolved_company_id,
    )
    return {
        "account_id": int(out.get("account_id", account_id)),
        "currency": norm_ccy_fn(out.get("currency", "")),
        "balance": to_dec_str_fn(out.get("balance", "0"), out.get("currency", "")),
    }


async def withdraw_flow(
    *,
    account_id: int,
    req: Any,
    x_request_id: str | None,
    current_user: User,
    db: AsyncSession,
    ensure_account_access_fn: Any,
    get_storage_fn: Any,
    require_kzt_integer_amount_fn: Any,
    norm_ccy_fn: Any,
    to_dec_str_fn: Any,
) -> dict[str, Any]:
    if is_platform_admin(current_user):
        raise AuthorizationError("Insufficient permissions", "FORBIDDEN")
    resolved_company_id = resolve_tenant_company_id(current_user, not_found_detail="Company not set")
    await ensure_account_access_fn(account_id, current_user, db)
    storage = await get_storage_fn(db)
    acc = await storage.get_account(account_id, company_id=resolved_company_id)
    if acc:
        require_kzt_integer_amount_fn(req.amount, acc.get("currency", ""))
    out = await storage.withdraw(
        account_id,
        req.amount,
        getattr(req, "reference", None),
        x_request_id,
        company_id=resolved_company_id,
    )
    return {
        "account_id": int(out.get("account_id", account_id)),
        "currency": norm_ccy_fn(out.get("currency", "")),
        "balance": to_dec_str_fn(out.get("balance", "0"), out.get("currency", "")),
    }


async def transfer_flow(
    *,
    req: Any,
    x_request_id: str | None,
    current_user: User,
    db: AsyncSession,
    ensure_account_access_fn: Any,
    ensure_user_in_company_fn: Any,
    get_storage_fn: Any,
    require_kzt_integer_amount_fn: Any,
    norm_ccy_fn: Any,
    to_dec_str_fn: Any,
) -> dict[str, Any]:
    if req.source_account_id == req.destination_account_id:
        raise HTTPException(status_code=400, detail="source and destination must differ")

    if is_platform_admin(current_user):
        raise AuthorizationError("Insufficient permissions", "FORBIDDEN")
    resolved_company_id = resolve_tenant_company_id(current_user, not_found_detail="Company not set")
    src_acc = await ensure_account_access_fn(req.source_account_id, current_user, db)
    dst_acc = await ensure_account_access_fn(req.destination_account_id, current_user, db)
    src_company = getattr(
        await ensure_user_in_company_fn(int(src_acc.get("user_id", 0)), current_user, db), "company_id", None
    )
    dst_company = getattr(
        await ensure_user_in_company_fn(int(dst_acc.get("user_id", 0)), current_user, db), "company_id", None
    )
    if src_company != dst_company or src_company != resolved_company_id:
        raise HTTPException(status_code=404, detail="account not found")

    require_kzt_integer_amount_fn(req.amount, src_acc.get("currency", ""))

    storage = await get_storage_fn(db)
    out = await storage.transfer(
        req.source_account_id,
        req.destination_account_id,
        req.amount,
        getattr(req, "reference", None),
        x_request_id,
        company_id=resolved_company_id,
    )
    src = out.get("source", {}) if isinstance(out, dict) else {}
    dst = out.get("destination", {}) if isinstance(out, dict) else {}
    return {
        "source": {
            "account_id": int(src.get("account_id", req.source_account_id)),
            "currency": norm_ccy_fn(src.get("currency", "")),
            "balance": to_dec_str_fn(src.get("balance", "0"), src.get("currency", "")),
        },
        "destination": {
            "account_id": int(dst.get("account_id", req.destination_account_id)),
            "currency": norm_ccy_fn(dst.get("currency", "")),
            "balance": to_dec_str_fn(dst.get("balance", "0"), dst.get("currency", "")),
        },
    }


async def ledger_flow(
    *,
    account_id: int,
    page: int,
    size: int,
    current_user: User,
    db: AsyncSession,
    ensure_account_access_fn: Any,
    get_storage_fn: Any,
    norm_ccy_fn: Any,
    to_dec_str_fn: Any,
) -> dict[str, Any]:
    if is_platform_admin(current_user):
        raise AuthorizationError("Insufficient permissions", "FORBIDDEN")
    resolved_company_id = resolve_tenant_company_id(current_user, not_found_detail="Company not set")
    await ensure_account_access_fn(account_id, current_user, db)
    storage = await get_storage_fn(db)
    page_obj = await storage.list_ledger(
        account_id,
        page,
        size,
        company_id=resolved_company_id,
    )
    items = page_obj.get("items", []) if isinstance(page_obj, dict) else []
    meta = (
        page_obj.get("meta", {"page": page, "size": size, "total": len(items)})
        if isinstance(page_obj, dict)
        else {"page": page, "size": size, "total": len(items)}
    )
    norm_items: list[dict[str, Any]] = []
    for it in items:
        norm_items.append(
            {
                "id": int(it.get("id")),
                "account_id": int(it.get("account_id", account_id)),
                "type": str(it.get("type", it.get("entry_type", ""))),
                "amount": to_dec_str_fn(it.get("amount", "0"), it.get("currency", "")),
                "currency": norm_ccy_fn(it.get("currency", "")),
                "reference": it.get("reference"),
                "created_at": str(it.get("created_at", "")),
            }
        )
    return {"items": norm_items, "meta": meta}


async def adjust_balance_flow(
    *,
    account_id: int,
    payload: Any,
    x_request_id: str | None,
    current_user: User,
    db: AsyncSession,
    ensure_account_access_fn: Any,
    get_storage_fn: Any,
    storage_caps_fn: Any,
    norm_ccy_fn: Any,
    to_dec_str_fn: Any,
) -> dict[str, Any]:
    storage = await get_storage_fn(db)
    caps = storage_caps_fn(storage)
    if not caps.get("has_adjust"):
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="adjust_balance not supported by storage",
        )

    if is_platform_admin(current_user):
        raise AuthorizationError("Insufficient permissions", "FORBIDDEN")
    resolved_company_id = resolve_tenant_company_id(current_user, not_found_detail="Company not set")
    await ensure_account_access_fn(account_id, current_user, db)
    out = await storage.adjust_balance(
        account_id,
        payload.new_balance,
        payload.reference,
        x_request_id,
        company_id=resolved_company_id,
    )
    return {
        "account_id": int(out.get("account_id", account_id)),
        "currency": norm_ccy_fn(out.get("currency", "")),
        "balance": to_dec_str_fn(out.get("balance", "0"), out.get("currency", "")),
    }
