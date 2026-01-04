"""
Product management endpoints (enterprise-ready).

- Полная фильтрация + безопасная пагинация и сортировка
- Массовые операции (bulk create/update/activate/deactivate)
- Подсказки поиска (suggest)
- Управление стоками, флагами (featured/active)
- Эндпоинты для категорий (list/get)
- Аудит-логи на ключевых изменениях
- Репрайсинг: конфиг + ручной тик
"""

from __future__ import annotations

from collections.abc import Callable
from enum import Enum
from typing import Any, TypeVar

from fastapi import APIRouter, Depends, Path, Query
from pydantic import BaseModel, Field, ValidationInfo, field_validator
from sqlalchemy import func, or_, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_async_db
from app.core.dependencies import (
    Pagination,
    api_rate_limit,
    get_current_verified_user,
    get_pagination,
)
from app.core.exceptions import AuthorizationError, ConflictError, NotFoundError, SmartSellValidationError
from app.core.logging import audit_logger
from app.models.product import Category, Product
from app.models.user import User
from app.schemas.base import PaginatedResponse, SuccessResponse
from app.schemas.product import ProductCreate, ProductResponse, ProductSearchFilters, ProductUpdate

# -------------------- Repricing service (мягкий импорт) --------------------

try:
    # Рекомендуемый сервис (мы его проектировали ранее)
    from app.services.repricing_service import (
        RepricingChannel,
        RepricingConfig,
        RepricingService,
        RepricingTickResult,
    )

    _repricing_available = True
except Exception:
    # Шим на случай, если сервис ещё не завезли: хранение в памяти (на время процесса)
    _repricing_available = False

    class RepricingChannel(str, Enum):
        kaspi = "kaspi"
        all = "all"

    class RepricingConfig(BaseModel):
        enabled: bool = False
        min: float | None = None
        max: float | None = None
        step: float = 1.0
        channel: RepricingChannel = RepricingChannel.kaspi
        friendly_ids: list[str] = []
        cooldown: int = 0
        hysteresis: float = 0.0

    class RepricingTickResult(BaseModel):
        current_price: float | None = None
        target_price: float | None = None
        best_competitor: dict[str, Any] | None = None
        reason: str = "repricing service not configured; using shim"

    class _ShimStore:
        cfg: dict[int, RepricingConfig] = {}

    class RepricingService:
        def __init__(self, db: Any):
            self.db = db

        def get_config(self, product_id: int) -> RepricingConfig:
            return _ShimStore.cfg.get(product_id, RepricingConfig())

        def set_config(self, product: Product, cfg: RepricingConfig) -> RepricingConfig:
            _ShimStore.cfg[product.id] = cfg
            return cfg

        def tick(
            self, product: Product, cfg: RepricingConfig | None = None, *, apply: bool = False
        ) -> RepricingTickResult:
            cfg = cfg or self.get_config(product.id)
            current = float(product.price) if product.price is not None else None
            target = current
            reason = "noop (shim)"
            return RepricingTickResult(
                current_price=current,
                target_price=target,
                best_competitor=None,
                reason=reason,
            )


router = APIRouter(prefix="/products", tags=["Products"], dependencies=[Depends(api_rate_limit)])

T = TypeVar("T")


async def _run_sync(db: AsyncSession, fn: Callable[[Any], T]) -> T:
    return await db.run_sync(fn)


# ---------------------------------------------------------------------------
# Вспомогательные утилиты
# ---------------------------------------------------------------------------

_ALLOWED_SORT_FIELDS = {
    "id": Product.id,
    "name": Product.name,
    "price": Product.price,
    "created_at": Product.created_at,
    "updated_at": Product.updated_at,
    "stock_quantity": Product.stock_quantity,
}


def _apply_filters(query, filters: ProductSearchFilters):
    if filters.category_id is not None:
        query = query.filter(Product.category_id == filters.category_id)

    if filters.min_price is not None:
        query = query.filter(Product.price >= filters.min_price)

    if filters.max_price is not None:
        query = query.filter(Product.price <= filters.max_price)

    if filters.is_active is not None:
        query = query.filter(Product.is_active == filters.is_active)

    if filters.is_featured is not None:
        query = query.filter(Product.is_featured == filters.is_featured)

    # В твоей схеме нет поля is_digital в модели Product — оставляю условие защитно:
    if hasattr(Product, "is_digital") and filters.is_digital is not None:
        query = query.filter(Product.is_digital == filters.is_digital)

    if filters.in_stock is not None:
        if filters.in_stock:
            query = query.filter(Product.stock_quantity > 0)
        else:
            query = query.filter(Product.stock_quantity == 0)

    if filters.search:
        s = f"%{filters.search}%"
        query = query.filter(
            or_(
                Product.name.ilike(s),
                Product.description.ilike(s),
                Product.sku.ilike(s),
            )
        )
    return query


def _apply_sorting(query, sort_by: str, sort_order: str):
    # По умолчанию — созданные недавно
    column = _ALLOWED_SORT_FIELDS.get(sort_by, Product.created_at)
    if sort_order.lower() == "asc":
        return query.order_by(column.asc())
    return query.order_by(column.desc())


def _is_admin(user: User) -> bool:
    """
    Универсальная проверка «админ» без знания точной модели ролей.
    Поддерживает поля: is_superuser, is_admin, role in ('admin','owner','superuser').
    """
    if getattr(user, "is_superuser", False) or getattr(user, "is_admin", False):
        return True
    role = getattr(user, "role", None)
    if isinstance(role, str) and role.lower() in {"admin", "owner", "superuser"}:
        return True
    return False


_PRODUCT_READ_ROLES = {"admin", "manager", "analyst", "storekeeper"}
_PRODUCT_WRITE_ROLES = {"admin", "manager"}
_STOCK_WRITE_ROLES = {"admin", "manager", "storekeeper"}


def _ensure_role(user: User, allowed: set[str]) -> None:
    role = (getattr(user, "role", "") or "").lower()
    if role == "platform_admin":
        return
    if role not in allowed:
        raise AuthorizationError("Insufficient permissions", "INSUFFICIENT_PERMISSIONS")


def _filter_company(query, user: User):
    cid = getattr(user, "company_id", None)
    if cid is not None:
        query = query.filter(Product.company_id == cid)
    return query


async def _get_product_or_404(db: AsyncSession, product_id: int, user: User) -> Product:
    def _load(session: Any) -> Product | None:
        query = session.query(Product).filter(Product.id == product_id)
        query = _filter_company(query, user)
        return query.first()

    product = await _run_sync(db, _load)
    if not product:
        raise NotFoundError("Product not found", "PRODUCT_NOT_FOUND")
    return product


# ---------------------------------------------------------------------------
# Основные CRUD
# ---------------------------------------------------------------------------


@router.get("", response_model=PaginatedResponse[ProductResponse])
async def list_products(
    filters: ProductSearchFilters = Depends(),
    pagination: Pagination = Depends(get_pagination),
    sort_by: str = Query("created_at", description=f"One of: {', '.join(_ALLOWED_SORT_FIELDS.keys())}"),
    sort_order: str = Query("desc", pattern="^(?i)(asc|desc)$"),
    current_user: User = Depends(get_current_verified_user),
    db: AsyncSession = Depends(get_async_db),
):
    """List products with filtering, sorting and pagination."""
    _ensure_role(current_user, _PRODUCT_READ_ROLES)

    def _sync_fetch(session: Any):
        query_local = _filter_company(session.query(Product), current_user)
        query_local = _apply_filters(query_local, filters)
        total_local = query_local.order_by(None).count()
        query_local = _apply_sorting(query_local, sort_by, sort_order)
        items_local = query_local.offset(pagination.offset).limit(pagination.limit).all()
        return total_local, items_local

    total, products = await _run_sync(db, _sync_fetch)

    return PaginatedResponse.create(items=products, total=total, page=pagination.page, per_page=pagination.per_page)


@router.get("/{product_id}", response_model=ProductResponse)
async def get_product(
    product_id: int = Path(..., ge=1),
    current_user: User = Depends(get_current_verified_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Get product by ID."""
    _ensure_role(current_user, _PRODUCT_READ_ROLES)
    return await _get_product_or_404(db, product_id, current_user)


@router.post("", response_model=ProductResponse)
async def create_product(
    product_data: ProductCreate,
    current_user: User = Depends(get_current_verified_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Create a new product."""
    _ensure_role(current_user, _PRODUCT_WRITE_ROLES)
    try:

        def _sync_create(session: Any) -> Product:
            if product_data.category_id:
                category = session.query(Category).filter(Category.id == product_data.category_id).first()
                if not category:
                    raise NotFoundError("Category not found", "CATEGORY_NOT_FOUND")

            payload = product_data.model_dump()
            if payload.get("sku"):
                payload["sku"] = payload["sku"].strip().upper()
            payload["company_id"] = getattr(current_user, "company_id", None)
            product_local = Product(**payload)
            session.add(product_local)
            session.flush()
            return product_local

        product = await _run_sync(db, _sync_create)
        await db.commit()
        await db.refresh(product)

        audit_logger.log_data_change(
            user_id=current_user.id,
            action="create",
            resource_type="product",
            resource_id=str(product.id),
            changes=product_data.model_dump(),
        )

        return product

    except IntegrityError as e:
        await db.rollback()
        msg = str(getattr(e, "orig", e))
        if "sku" in msg:
            raise ConflictError("Product with this SKU already exists", "DUPLICATE_SKU")
        if "slug" in msg:
            raise ConflictError("Product with this slug already exists", "DUPLICATE_SLUG")
        raise ConflictError("Product creation failed due to data conflict", "CREATION_FAILED")


@router.put("/{product_id}", response_model=ProductResponse)
async def update_product(
    product_id: int,
    product_update: ProductUpdate,
    current_user: User = Depends(get_current_verified_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Update product by ID."""
    _ensure_role(current_user, _PRODUCT_WRITE_ROLES)
    product = await _get_product_or_404(db, product_id, current_user)

    try:
        changes: dict[str, Any] = {}

        for field, value in product_update.model_dump(exclude_unset=True).items():
            if hasattr(product, field):
                if field == "sku" and value is not None:
                    value = value.strip().upper()
                old = getattr(product, field)
                if old != value:
                    setattr(product, field, value)
                    changes[field] = {"old": old, "new": value}

        if changes:
            await db.commit()
            await db.refresh(product)
            audit_logger.log_data_change(
                user_id=current_user.id,
                action="update",
                resource_type="product",
                resource_id=str(product.id),
                changes=changes,
            )

        return product

    except IntegrityError as e:
        await db.rollback()
        msg = str(getattr(e, "orig", e))
        if "sku" in msg:
            raise ConflictError("Product with this SKU already exists", "DUPLICATE_SKU")
        if "slug" in msg:
            raise ConflictError("Product with this slug already exists", "DUPLICATE_SLUG")
        raise ConflictError("Product update failed due to data conflict", "UPDATE_FAILED")


@router.delete("/{product_id}", response_model=SuccessResponse)
async def delete_product(
    product_id: int,
    current_user: User = Depends(get_current_verified_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Soft delete product by setting is_active=False."""
    _ensure_role(current_user, _PRODUCT_WRITE_ROLES)
    product = await _get_product_or_404(db, product_id, current_user)

    if not product.is_active:
        return SuccessResponse(message="Product already inactive")

    old_state = product.is_active
    product.is_active = False
    await db.commit()

    audit_logger.log_data_change(
        user_id=current_user.id,
        action="delete",
        resource_type="product",
        resource_id=str(product.id),
        changes={"is_active": {"old": old_state, "new": False}},
    )

    return SuccessResponse(message="Product deleted successfully")


# ---------------------------------------------------------------------------
# Stock
# ---------------------------------------------------------------------------


@router.get("/{product_id}/stock", response_model=dict)
async def get_product_stock(
    product_id: int,
    current_user: User = Depends(get_current_verified_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Get product stock information."""
    _ensure_role(current_user, _PRODUCT_READ_ROLES)
    product = await _get_product_or_404(db, product_id, current_user)

    return {
        "product_id": product.id,
        "stock_quantity": product.stock_quantity,
        "min_stock_level": getattr(product, "min_stock_level", None),
        "max_stock_level": getattr(product, "max_stock_level", None),
        "in_stock": (product.stock_quantity or 0) > 0,
        "low_stock": (
            product.stock_quantity is not None
            and getattr(product, "min_stock_level", None) is not None
            and product.stock_quantity <= product.min_stock_level
        ),
    }


@router.put("/{product_id}/stock", response_model=SuccessResponse)
async def update_product_stock(
    product_id: int,
    stock_quantity: int = Query(..., ge=0, description="New stock quantity"),
    current_user: User = Depends(get_current_verified_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Update product stock quantity."""
    _ensure_role(current_user, _STOCK_WRITE_ROLES)
    product = await _get_product_or_404(db, product_id, current_user)

    old_quantity = product.stock_quantity
    product.stock_quantity = stock_quantity
    await db.commit()

    audit_logger.log_data_change(
        user_id=current_user.id,
        action="update_stock",
        resource_type="product",
        resource_id=str(product.id),
        changes={"stock_quantity": {"old": old_quantity, "new": stock_quantity}},
    )

    return SuccessResponse(message="Stock updated successfully", data={"new_quantity": stock_quantity})


# ---------------------------------------------------------------------------
# Feature toggles / Active toggles
# ---------------------------------------------------------------------------


@router.post("/{product_id}/feature", response_model=SuccessResponse)
async def set_featured(
    product_id: int,
    featured: bool = Query(..., description="Set featured flag"),
    current_user: User = Depends(get_current_verified_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Set or unset product's featured flag."""
    _ensure_role(current_user, _PRODUCT_WRITE_ROLES)
    product = await _get_product_or_404(db, product_id, current_user)

    old = product.is_featured
    if old == featured:
        return SuccessResponse(message="No changes", data={"featured": featured})

    product.is_featured = featured
    await db.commit()

    audit_logger.log_data_change(
        user_id=current_user.id,
        action="feature_toggle",
        resource_type="product",
        resource_id=str(product.id),
        changes={"is_featured": {"old": old, "new": featured}},
    )
    return SuccessResponse(message="Updated", data={"featured": featured})


@router.post("/{product_id}/activate", response_model=SuccessResponse)
async def activate_product(
    product_id: int,
    current_user: User = Depends(get_current_verified_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Activate a product (is_active=True)."""
    _ensure_role(current_user, _PRODUCT_WRITE_ROLES)
    product = await _get_product_or_404(db, product_id, current_user)
    if product.is_active:
        return SuccessResponse(message="Product already active")

    old = product.is_active
    product.is_active = True
    await db.commit()

    audit_logger.log_data_change(
        user_id=current_user.id,
        action="activate",
        resource_type="product",
        resource_id=str(product.id),
        changes={"is_active": {"old": old, "new": True}},
    )
    return SuccessResponse(message="Product activated")


@router.post("/{product_id}/deactivate", response_model=SuccessResponse)
async def deactivate_product(
    product_id: int,
    current_user: User = Depends(get_current_verified_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Deactivate a product (is_active=False)."""
    _ensure_role(current_user, _PRODUCT_WRITE_ROLES)
    product = await _get_product_or_404(db, product_id, current_user)
    if not product.is_active:
        return SuccessResponse(message="Product already inactive")

    old = product.is_active
    product.is_active = False
    await db.commit()

    audit_logger.log_data_change(
        user_id=current_user.id,
        action="deactivate",
        resource_type="product",
        resource_id=str(product.id),
        changes={"is_active": {"old": old, "new": False}},
    )
    return SuccessResponse(message="Product deactivated")


# ---------------------------------------------------------------------------
# Bulk operations
# ---------------------------------------------------------------------------


@router.post("/bulk/create", response_model=SuccessResponse)
async def bulk_create_products(
    payload: list[ProductCreate],
    current_user: User = Depends(get_current_verified_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Bulk create products. Возвращает счётчики и ошибки по индексам."""
    _ensure_role(current_user, _PRODUCT_WRITE_ROLES)
    created = 0
    errors: list[dict[str, Any]] = []

    for idx, data in enumerate(payload):
        try:
            if data.category_id:
                category_id = data.category_id
                cat = await _run_sync(
                    db,
                    lambda session, category_id=category_id: session.query(Category)
                    .filter(Category.id == category_id)
                    .first(),
                )
                if not cat:
                    errors.append({"index": idx, "error": "CATEGORY_NOT_FOUND"})
                    continue

            row = data.model_dump()
            if row.get("sku"):
                row["sku"] = row["sku"].strip().upper()
            obj = Product(**row, company_id=getattr(current_user, "company_id", None))
            db.add(obj)
            await db.flush()  # чтобы поймать ошибки уникальности до коммита
            created += 1

            audit_logger.log_data_change(
                user_id=current_user.id,
                action="create",
                resource_type="product",
                resource_id="pending",
                changes=data.model_dump(),
            )
        except IntegrityError as e:
            await db.rollback()
            msg = str(getattr(e, "orig", e))
            code = "DUPLICATE"
            if "sku" in msg:
                code = "DUPLICATE_SKU"
            elif "slug" in msg:
                code = "DUPLICATE_SLUG"
            errors.append({"index": idx, "error": code})

    await db.commit()
    return SuccessResponse(message="Bulk create finished", data={"created": created, "errors": errors})


@router.post("/bulk/update", response_model=SuccessResponse)
async def bulk_update_products(
    payload: list[dict[str, Any]],
    current_user: User = Depends(get_current_verified_user),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Bulk update products.
    Формат элемента: {"id": int, <поле>: <значение>, ...}
    """
    updated = 0
    errors: list[dict[str, Any]] = []

    for idx, item in enumerate(payload):
        pid = item.get("id")
        if not isinstance(pid, int) or pid <= 0:
            errors.append({"index": idx, "error": "INVALID_ID"})
            continue

        product = await _run_sync(
            db, lambda session, product_id=pid: session.query(Product).filter(Product.id == product_id).first()
        )
        if not product:
            errors.append({"index": idx, "error": "PRODUCT_NOT_FOUND", "id": pid})
            continue

        fields = {k: v for k, v in item.items() if k != "id"}
        if not fields:
            continue

        try:
            changes = {}
            for k, v in fields.items():
                if hasattr(product, k) and getattr(product, k) != v:
                    changes[k] = {"old": getattr(product, k), "new": v}
                    setattr(product, k, v)

            if changes:
                await db.flush()
                updated += 1
                audit_logger.log_data_change(
                    user_id=current_user.id,
                    action="update",
                    resource_type="product",
                    resource_id=str(product.id),
                    changes=changes,
                )
        except IntegrityError as e:
            await db.rollback()
            msg = str(getattr(e, "orig", e))
            code = "UPDATE_FAILED"
            if "sku" in msg:
                code = "DUPLICATE_SKU"
            elif "slug" in msg:
                code = "DUPLICATE_SLUG"
            errors.append({"index": idx, "error": code, "id": pid})

    await db.commit()
    return SuccessResponse(message="Bulk update finished", data={"updated": updated, "errors": errors})


@router.post("/bulk/activate", response_model=SuccessResponse)
async def bulk_activate_products(
    ids: list[int],
    current_user: User = Depends(get_current_verified_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Bulk activate products."""
    if not ids:
        raise SmartSellValidationError("No ids provided", "NO_IDS")

    result = await db.execute(
        update(Product).where(Product.id.in_(ids), Product.is_active.is_(False)).values(is_active=True)
    )
    affected = int(result.rowcount or 0)
    await db.commit()

    if affected:
        audit_logger.log_data_change(
            user_id=current_user.id,
            action="bulk_activate",
            resource_type="product",
            resource_id="*",
            changes={"count": affected, "ids": ids},
        )
    return SuccessResponse(message="Bulk activate finished", data={"activated": affected})


@router.post("/bulk/deactivate", response_model=SuccessResponse)
async def bulk_deactivate_products(
    ids: list[int],
    current_user: User = Depends(get_current_verified_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Bulk deactivate products."""
    if not ids:
        raise SmartSellValidationError("No ids provided", "NO_IDS")

    result = await db.execute(
        update(Product).where(Product.id.in_(ids), Product.is_active.is_(True)).values(is_active=False)
    )
    affected = int(result.rowcount or 0)
    await db.commit()

    if affected:
        audit_logger.log_data_change(
            user_id=current_user.id,
            action="bulk_deactivate",
            resource_type="product",
            resource_id="*",
            changes={"count": affected, "ids": ids},
        )
    return SuccessResponse(message="Bulk deactivate finished", data={"deactivated": affected})


# ---------------------------------------------------------------------------
# Search helpers / suggestions
# ---------------------------------------------------------------------------


@router.get("/_suggest", response_model=list[str])
async def product_suggest(
    q: str = Query("", min_length=0),
    limit: int = Query(10, ge=1, le=50),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Suggest product names/sku by prefix/substring (case-insensitive).
    Возвращает список строк подсказок.
    """
    if not q:
        return []

    pattern = f"%{q}%"
    rows = await _run_sync(
        db,
        lambda session: session.query(Product.name)
        .filter(Product.name.ilike(pattern))
        .order_by(Product.name.asc())
        .limit(limit)
        .all(),
    )
    # Расширение: если не нашли по name, пробуем sku
    if not rows:
        rows = await _run_sync(
            db,
            lambda session: session.query(Product.sku)
            .filter(Product.sku.ilike(pattern))
            .order_by(Product.sku.asc())
            .limit(limit)
            .all(),
        )
    # sqlalchemy возвращает список кортежей
    return [r[0] for r in rows if r and r[0]]


# ---------------------------------------------------------------------------
# Categories
# ---------------------------------------------------------------------------


@router.get("/categories", response_model=list[dict[str, Any]])
async def list_categories(
    db: AsyncSession = Depends(get_async_db),
    only_active: bool = Query(False, description="Return only categories that have active products"),
):
    """
    Список категорий. Если only_active=True — возвращаем только те, где есть активные товары.
    """

    def _sync_list(session: Any):
        q = session.query(Category)
        if only_active:
            q = (
                q.join(Product, Product.category_id == Category.id)
                .filter(Product.is_active.is_(True))
                .group_by(Category.id)
            )
        return q.order_by(Category.name.asc()).all()

    cats = await _run_sync(db, _sync_list)
    return [{"id": c.id, "name": c.name, "slug": getattr(c, "slug", None)} for c in cats]


@router.get("/categories/{category_id}", response_model=dict[str, Any])
async def get_category(
    category_id: int = Path(..., ge=1),
    with_counts: bool = Query(True),
    db: AsyncSession = Depends(get_async_db),
):
    """Получить категорию; опционально — с количеством товаров."""
    cat = await _run_sync(db, lambda session: session.query(Category).filter(Category.id == category_id).first())
    if not cat:
        raise NotFoundError("Category not found", "CATEGORY_NOT_FOUND")

    payload: dict[str, Any] = {"id": cat.id, "name": cat.name, "slug": getattr(cat, "slug", None)}
    if with_counts:
        counts = await _run_sync(
            db,
            lambda session: session.query(func.count(Product.id))
            .filter(Product.category_id == category_id, Product.is_active.is_(True))
            .scalar(),
        )
        payload["active_products"] = int(counts or 0)
    return payload


# ---------------------------------------------------------------------------
# Repricing (новые эндпоинты)
# ---------------------------------------------------------------------------


class RepricingConfigIn(BaseModel):
    enabled: bool = Field(False, description="Включить автоматический демпинг для товара")
    min: float | None = Field(None, ge=0, description="Минимальная цена, ниже которой опускаться нельзя")
    max: float | None = Field(None, ge=0, description="Максимальная цена, выше которой подниматься не надо")
    step: float = Field(1.0, gt=0, description="Шаг изменения цены (например 1 или 10 тенге)")
    channel: RepricingChannel = Field(RepricingChannel.kaspi, description="Целевой канал")
    friendly_ids: list[str] = Field(default_factory=list, description="ID магазинов-друзей, с кем не демпингуем")
    cooldown: int = Field(0, ge=0, description="Кулдаун в секундах между изменениями")
    hysteresis: float = Field(0.0, ge=0, description="Гистерезис на флуктуации (минимальный шаг реакции)")

    @field_validator("max", mode="after")
    def _max_vs_min(cls, v, info: ValidationInfo):
        mn = (info.data or {}).get("min")
        if v is not None and mn is not None and v < mn:
            raise ValueError("max must be >= min")
        return v


class RepricingConfigOut(RepricingConfigIn):
    # Возвращаем то же самое (можно добавить служебные поля в будущем)
    pass


class RepricingTickOut(BaseModel):
    current_price: float | None
    target_price: float | None
    best_competitor: dict[str, Any] | None
    reason: str
    applied: bool = False


@router.put("/{product_id}/repricing/config", response_model=RepricingConfigOut)
async def set_repricing_config(
    product_id: int,
    cfg: RepricingConfigIn,
    current_user: User = Depends(get_current_verified_user),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Установить конфиг репрайсинга товара:
      - enabled, min, max, step, channel, friendly_ids, cooldown, hysteresis
    Дополнительно синхронизируем часть полей в самом товаре:
      - enable_price_dumping <- enabled
      - min_price <- min (если указано)
      - max_price <- max (если указано)
    """

    def _sync_apply(session: Any):
        product_local = session.query(Product).filter(Product.id == product_id).first()
        if not product_local:
            return None, None, {}

        service = RepricingService(session)
        saved_local = service.set_config(product_local, RepricingConfig(**cfg.model_dump()))

        changes_local: dict[str, Any] = {}
        if hasattr(product_local, "enable_price_dumping"):
            old = product_local.enable_price_dumping
            if old != cfg.enabled:
                product_local.enable_price_dumping = cfg.enabled
                changes_local["enable_price_dumping"] = {"old": old, "new": cfg.enabled}

        if cfg.min is not None and hasattr(product_local, "min_price"):
            old = float(product_local.min_price) if product_local.min_price is not None else None
            if old != cfg.min:
                product_local.min_price = cfg.min
                changes_local["min_price"] = {"old": old, "new": cfg.min}

        if cfg.max is not None and hasattr(product_local, "max_price"):
            old = float(product_local.max_price) if product_local.max_price is not None else None
            if old != cfg.max:
                product_local.max_price = cfg.max
                changes_local["max_price"] = {"old": old, "new": cfg.max}

        return product_local, saved_local, changes_local

    product, saved, changes = await _run_sync(db, _sync_apply)
    if not product or not saved:
        raise NotFoundError("Product not found", "PRODUCT_NOT_FOUND")

    await db.commit()

    if changes:
        audit_logger.log_data_change(
            user_id=current_user.id,
            action="repricing_config",
            resource_type="product",
            resource_id=str(product.id),
            changes=changes | {"repricing_config": cfg.model_dump()},
        )

    return RepricingConfigOut(**saved.model_dump())


@router.post("/{product_id}/repricing/tick", response_model=RepricingTickOut)
async def repricing_tick(
    product_id: int,
    apply: bool = Query(False, description="Применить рассчитанную цену сразу к товару"),
    current_user: User = Depends(get_current_verified_user),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Ручной тик репрайсера (для отладки). Только для админов.
    Возвращает: текущую цену, целевую, лучшего конкурента, причину.
    Если apply=True — применяет рассчитанную цену.
    """
    if not _is_admin(current_user):
        raise SmartSellValidationError("Forbidden: admin only", "FORBIDDEN")

    def _sync_tick(session: Any):
        product_local = session.query(Product).filter(Product.id == product_id).first()
        if not product_local:
            return None, None, False, None, None

        service = RepricingService(session)
        result_local: RepricingTickResult = service.tick(product_local, service.get_config(product_id), apply=apply)

        applied_local = False
        price_change: dict[str, Any] | None = None
        if apply and result_local.target_price is not None:
            old_price = float(product_local.price) if product_local.price is not None else None
            new_price = float(result_local.target_price)
            if old_price != new_price:
                product_local.price = new_price
                applied_local = True
                price_change = {"old": old_price, "new": new_price}

        return product_local, result_local, applied_local, price_change

    product, result, applied, price_change = await _run_sync(db, _sync_tick)
    if not product or not result:
        raise NotFoundError("Product not found", "PRODUCT_NOT_FOUND")

    if applied:
        await db.commit()
        if price_change:
            audit_logger.log_data_change(
                user_id=current_user.id,
                action="repricing_apply",
                resource_type="product",
                resource_id=str(product.id),
                changes={"price": price_change},
            )

    return RepricingTickOut(
        current_price=result.current_price,
        target_price=result.target_price,
        best_competitor=result.best_competitor,
        reason=result.reason,
        applied=applied,
    )
