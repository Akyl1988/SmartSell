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

from enum import Enum
from typing import Any, Optional

from fastapi import APIRouter, Depends, Path, Query
from pydantic import BaseModel, Field, validator
from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.dependencies import (
    Pagination,
    api_rate_limit,
    get_current_verified_user,
    get_pagination,
)
from app.core.exceptions import ConflictError, NotFoundError, SmartSellValidationError
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
        min: Optional[float] = None
        max: Optional[float] = None
        step: float = 1.0
        channel: RepricingChannel = RepricingChannel.kaspi
        friendly_ids: list[str] = []
        cooldown: int = 0
        hysteresis: float = 0.0

    class RepricingTickResult(BaseModel):
        current_price: Optional[float] = None
        target_price: Optional[float] = None
        best_competitor: Optional[dict[str, Any]] = None
        reason: str = "repricing service not configured; using shim"

    class _ShimStore:
        cfg: dict[int, RepricingConfig] = {}

    class RepricingService:
        def __init__(self, db: Session):
            self.db = db

        def get_config(self, product_id: int) -> RepricingConfig:
            return _ShimStore.cfg.get(product_id, RepricingConfig())

        def set_config(self, product: Product, cfg: RepricingConfig) -> RepricingConfig:
            _ShimStore.cfg[product.id] = cfg
            return cfg

        def tick(
            self, product: Product, cfg: Optional[RepricingConfig] = None, *, apply: bool = False
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


# ---------------------------------------------------------------------------
# Основные CRUD
# ---------------------------------------------------------------------------


@router.get("", response_model=PaginatedResponse[ProductResponse])
async def list_products(
    filters: ProductSearchFilters = Depends(),
    pagination: Pagination = Depends(get_pagination),
    sort_by: str = Query(
        "created_at", description=f"One of: {', '.join(_ALLOWED_SORT_FIELDS.keys())}"
    ),
    sort_order: str = Query("desc", pattern="^(?i)(asc|desc)$"),
    db: Session = Depends(get_db),
):
    """List products with filtering, sorting and pagination."""
    query = db.query(Product)
    query = _apply_filters(query, filters)

    # Всегда обнуляем order_by перед count (на всякий случай)
    total = query.order_by(None).count()

    query = _apply_sorting(query, sort_by, sort_order)
    products = query.offset(pagination.offset).limit(pagination.limit).all()

    return PaginatedResponse.create(
        items=products, total=total, page=pagination.page, per_page=pagination.per_page
    )


@router.get("/{product_id}", response_model=ProductResponse)
async def get_product(
    product_id: int = Path(..., ge=1),
    db: Session = Depends(get_db),
):
    """Get product by ID."""
    product = db.query(Product).filter(Product.id == product_id).first()
    if not product:
        raise NotFoundError("Product not found", "PRODUCT_NOT_FOUND")
    return product


@router.post("", response_model=ProductResponse)
async def create_product(
    product_data: ProductCreate,
    current_user: User = Depends(get_current_verified_user),
    db: Session = Depends(get_db),
):
    """Create a new product."""
    try:
        # Validate category exists if provided
        if product_data.category_id:
            category = db.query(Category).filter(Category.id == product_data.category_id).first()
            if not category:
                raise NotFoundError("Category not found", "CATEGORY_NOT_FOUND")

        product = Product(**product_data.dict())
        db.add(product)
        db.commit()
        db.refresh(product)

        audit_logger.log_data_change(
            user_id=current_user.id,
            action="create",
            resource_type="product",
            resource_id=str(product.id),
            changes=product_data.dict(),
        )

        return product

    except IntegrityError as e:
        db.rollback()
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
    db: Session = Depends(get_db),
):
    """Update product by ID."""
    product = db.query(Product).filter(Product.id == product_id).first()
    if not product:
        raise NotFoundError("Product not found", "PRODUCT_NOT_FOUND")

    try:
        changes: dict[str, Any] = {}

        for field, value in product_update.dict(exclude_unset=True).items():
            if hasattr(product, field):
                old = getattr(product, field)
                if old != value:
                    setattr(product, field, value)
                    changes[field] = {"old": old, "new": value}

        if changes:
            db.commit()
            db.refresh(product)
            audit_logger.log_data_change(
                user_id=current_user.id,
                action="update",
                resource_type="product",
                resource_id=str(product.id),
                changes=changes,
            )

        return product

    except IntegrityError as e:
        db.rollback()
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
    db: Session = Depends(get_db),
):
    """Soft delete product by setting is_active=False."""
    product = db.query(Product).filter(Product.id == product_id).first()
    if not product:
        raise NotFoundError("Product not found", "PRODUCT_NOT_FOUND")

    if not product.is_active:
        return SuccessResponse(message="Product already inactive")

    old_state = product.is_active
    product.is_active = False
    db.commit()

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
    db: Session = Depends(get_db),
):
    """Get product stock information."""
    product = db.query(Product).filter(Product.id == product_id).first()
    if not product:
        raise NotFoundError("Product not found", "PRODUCT_NOT_FOUND")

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
    db: Session = Depends(get_db),
):
    """Update product stock quantity."""
    product = db.query(Product).filter(Product.id == product_id).first()
    if not product:
        raise NotFoundError("Product not found", "PRODUCT_NOT_FOUND")

    old_quantity = product.stock_quantity
    product.stock_quantity = stock_quantity
    db.commit()

    audit_logger.log_data_change(
        user_id=current_user.id,
        action="update_stock",
        resource_type="product",
        resource_id=str(product.id),
        changes={"stock_quantity": {"old": old_quantity, "new": stock_quantity}},
    )

    return SuccessResponse(
        message="Stock updated successfully", data={"new_quantity": stock_quantity}
    )


# ---------------------------------------------------------------------------
# Feature toggles / Active toggles
# ---------------------------------------------------------------------------


@router.post("/{product_id}/feature", response_model=SuccessResponse)
async def set_featured(
    product_id: int,
    featured: bool = Query(..., description="Set featured flag"),
    current_user: User = Depends(get_current_verified_user),
    db: Session = Depends(get_db),
):
    """Set or unset product's featured flag."""
    product = db.query(Product).filter(Product.id == product_id).first()
    if not product:
        raise NotFoundError("Product not found", "PRODUCT_NOT_FOUND")

    old = product.is_featured
    if old == featured:
        return SuccessResponse(message="No changes", data={"featured": featured})

    product.is_featured = featured
    db.commit()

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
    db: Session = Depends(get_db),
):
    """Activate a product (is_active=True)."""
    product = db.query(Product).filter(Product.id == product_id).first()
    if not product:
        raise NotFoundError("Product not found", "PRODUCT_NOT_FOUND")
    if product.is_active:
        return SuccessResponse(message="Product already active")

    old = product.is_active
    product.is_active = True
    db.commit()

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
    db: Session = Depends(get_db),
):
    """Deactivate a product (is_active=False)."""
    product = db.query(Product).filter(Product.id == product_id).first()
    if not product:
        raise NotFoundError("Product not found", "PRODUCT_NOT_FOUND")
    if not product.is_active:
        return SuccessResponse(message="Product already inactive")

    old = product.is_active
    product.is_active = False
    db.commit()

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
    db: Session = Depends(get_db),
):
    """Bulk create products. Возвращает счётчики и ошибки по индексам."""
    created = 0
    errors: list[dict[str, Any]] = []

    for idx, data in enumerate(payload):
        try:
            if data.category_id:
                cat = db.query(Category).filter(Category.id == data.category_id).first()
                if not cat:
                    errors.append({"index": idx, "error": "CATEGORY_NOT_FOUND"})
                    continue

            obj = Product(**data.dict())
            db.add(obj)
            db.flush()  # чтобы поймать ошибки уникальности до коммита
            created += 1

            audit_logger.log_data_change(
                user_id=current_user.id,
                action="create",
                resource_type="product",
                resource_id="pending",
                changes=data.dict(),
            )
        except IntegrityError as e:
            db.rollback()
            msg = str(getattr(e, "orig", e))
            code = "DUPLICATE"
            if "sku" in msg:
                code = "DUPLICATE_SKU"
            elif "slug" in msg:
                code = "DUPLICATE_SLUG"
            errors.append({"index": idx, "error": code})

    db.commit()
    return SuccessResponse(
        message="Bulk create finished", data={"created": created, "errors": errors}
    )


@router.post("/bulk/update", response_model=SuccessResponse)
async def bulk_update_products(
    payload: list[dict[str, Any]],
    current_user: User = Depends(get_current_verified_user),
    db: Session = Depends(get_db),
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

        product = db.query(Product).filter(Product.id == pid).first()
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
                db.flush()
                updated += 1
                audit_logger.log_data_change(
                    user_id=current_user.id,
                    action="update",
                    resource_type="product",
                    resource_id=str(product.id),
                    changes=changes,
                )
        except IntegrityError as e:
            db.rollback()
            msg = str(getattr(e, "orig", e))
            code = "UPDATE_FAILED"
            if "sku" in msg:
                code = "DUPLICATE_SKU"
            elif "slug" in msg:
                code = "DUPLICATE_SLUG"
            errors.append({"index": idx, "error": code, "id": pid})

    db.commit()
    return SuccessResponse(
        message="Bulk update finished", data={"updated": updated, "errors": errors}
    )


@router.post("/bulk/activate", response_model=SuccessResponse)
async def bulk_activate_products(
    ids: list[int],
    current_user: User = Depends(get_current_verified_user),
    db: Session = Depends(get_db),
):
    """Bulk activate products."""
    if not ids:
        raise SmartSellValidationError("No ids provided", "NO_IDS")

    affected = (
        db.query(Product)
        .filter(Product.id.in_(ids), Product.is_active.is_(False))
        .update({Product.is_active: True}, synchronize_session=False)
    )
    db.commit()

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
    db: Session = Depends(get_db),
):
    """Bulk deactivate products."""
    if not ids:
        raise SmartSellValidationError("No ids provided", "NO_IDS")

    affected = (
        db.query(Product)
        .filter(Product.id.in_(ids), Product.is_active.is_(True))
        .update({Product.is_active: False}, synchronize_session=False)
    )
    db.commit()

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
    db: Session = Depends(get_db),
):
    """
    Suggest product names/sku by prefix/substring (case-insensitive).
    Возвращает список строк подсказок.
    """
    if not q:
        return []

    pattern = f"%{q}%"
    rows = (
        db.query(Product.name)
        .filter(Product.name.ilike(pattern))
        .order_by(Product.name.asc())
        .limit(limit)
        .all()
    )
    # Расширение: если не нашли по name, пробуем sku
    if not rows:
        rows = (
            db.query(Product.sku)
            .filter(Product.sku.ilike(pattern))
            .order_by(Product.sku.asc())
            .limit(limit)
            .all()
        )
    # sqlalchemy возвращает список кортежей
    return [r[0] for r in rows if r and r[0]]


# ---------------------------------------------------------------------------
# Categories
# ---------------------------------------------------------------------------


@router.get("/categories", response_model=list[dict[str, Any]])
async def list_categories(
    db: Session = Depends(get_db),
    only_active: bool = Query(
        False, description="Return only categories that have active products"
    ),
):
    """
    Список категорий. Если only_active=True — возвращаем только те, где есть активные товары.
    """
    q = db.query(Category)
    if only_active:
        q = (
            q.join(Product, Product.category_id == Category.id)
            .filter(Product.is_active.is_(True))
            .group_by(Category.id)
        )
    cats = q.order_by(Category.name.asc()).all()
    return [{"id": c.id, "name": c.name, "slug": getattr(c, "slug", None)} for c in cats]


@router.get("/categories/{category_id}", response_model=dict[str, Any])
async def get_category(
    category_id: int = Path(..., ge=1),
    with_counts: bool = Query(True),
    db: Session = Depends(get_db),
):
    """Получить категорию; опционально — с количеством товаров."""
    cat = db.query(Category).filter(Category.id == category_id).first()
    if not cat:
        raise NotFoundError("Category not found", "CATEGORY_NOT_FOUND")

    payload: dict[str, Any] = {"id": cat.id, "name": cat.name, "slug": getattr(cat, "slug", None)}
    if with_counts:
        counts = (
            db.query(func.count(Product.id))
            .filter(Product.category_id == category_id, Product.is_active.is_(True))
            .scalar()
        )
        payload["active_products"] = int(counts or 0)
    return payload


# ---------------------------------------------------------------------------
# Repricing (новые эндпоинты)
# ---------------------------------------------------------------------------


class RepricingConfigIn(BaseModel):
    enabled: bool = Field(False, description="Включить автоматический демпинг для товара")
    min: Optional[float] = Field(
        None, ge=0, description="Минимальная цена, ниже которой опускаться нельзя"
    )
    max: Optional[float] = Field(
        None, ge=0, description="Максимальная цена, выше которой подниматься не надо"
    )
    step: float = Field(1.0, gt=0, description="Шаг изменения цены (например 1 или 10 тенге)")
    channel: RepricingChannel = Field(RepricingChannel.kaspi, description="Целевой канал")
    friendly_ids: list[str] = Field(
        default_factory=list, description="ID магазинов-друзей, с кем не демпингуем"
    )
    cooldown: int = Field(0, ge=0, description="Кулдаун в секундах между изменениями")
    hysteresis: float = Field(
        0.0, ge=0, description="Гистерезис на флуктуации (минимальный шаг реакции)"
    )

    @validator("max")
    def _max_vs_min(cls, v, values):
        mn = values.get("min")
        if v is not None and mn is not None and v < mn:
            raise ValueError("max must be >= min")
        return v


class RepricingConfigOut(RepricingConfigIn):
    # Возвращаем то же самое (можно добавить служебные поля в будущем)
    pass


class RepricingTickOut(BaseModel):
    current_price: Optional[float]
    target_price: Optional[float]
    best_competitor: Optional[dict[str, Any]]
    reason: str
    applied: bool = False


@router.put("/{product_id}/repricing/config", response_model=RepricingConfigOut)
async def set_repricing_config(
    product_id: int,
    cfg: RepricingConfigIn,
    current_user: User = Depends(get_current_verified_user),
    db: Session = Depends(get_db),
):
    """
    Установить конфиг репрайсинга товара:
      - enabled, min, max, step, channel, friendly_ids, cooldown, hysteresis
    Дополнительно синхронизируем часть полей в самом товаре:
      - enable_price_dumping <- enabled
      - min_price <- min (если указано)
      - max_price <- max (если указано)
    """
    product = db.query(Product).filter(Product.id == product_id).first()
    if not product:
        raise NotFoundError("Product not found", "PRODUCT_NOT_FOUND")

    service = RepricingService(db)

    # Сохраняем конфиг через сервис
    saved = service.set_config(product, RepricingConfig(**cfg.dict()))

    # Синхронизируем с моделью Product, где это возможно
    changes: dict[str, Any] = {}
    if hasattr(product, "enable_price_dumping"):
        old = product.enable_price_dumping
        if old != cfg.enabled:
            product.enable_price_dumping = cfg.enabled
            changes["enable_price_dumping"] = {"old": old, "new": cfg.enabled}

    if cfg.min is not None and hasattr(product, "min_price"):
        old = float(product.min_price) if product.min_price is not None else None
        if old != cfg.min:
            product.min_price = cfg.min
            changes["min_price"] = {"old": old, "new": cfg.min}

    if cfg.max is not None and hasattr(product, "max_price"):
        old = float(product.max_price) if product.max_price is not None else None
        if old != cfg.max:
            product.max_price = cfg.max
            changes["max_price"] = {"old": old, "new": cfg.max}

    if changes:
        db.commit()
        audit_logger.log_data_change(
            user_id=current_user.id,
            action="repricing_config",
            resource_type="product",
            resource_id=str(product.id),
            changes=changes | {"repricing_config": cfg.dict()},
        )

    return RepricingConfigOut(**saved.dict())


@router.post("/{product_id}/repricing/tick", response_model=RepricingTickOut)
async def repricing_tick(
    product_id: int,
    apply: bool = Query(False, description="Применить рассчитанную цену сразу к товару"),
    current_user: User = Depends(get_current_verified_user),
    db: Session = Depends(get_db),
):
    """
    Ручной тик репрайсера (для отладки). Только для админов.
    Возвращает: текущую цену, целевую, лучшего конкурента, причину.
    Если apply=True — применяет рассчитанную цену.
    """
    if not _is_admin(current_user):
        raise SmartSellValidationError("Forbidden: admin only", "FORBIDDEN")

    product = db.query(Product).filter(Product.id == product_id).first()
    if not product:
        raise NotFoundError("Product not found", "PRODUCT_NOT_FOUND")

    service = RepricingService(db)
    cfg = service.get_config(product_id)

    result: RepricingTickResult = service.tick(product, cfg, apply=apply)

    applied = False
    if apply and result.target_price is not None:
        # Применяем цену, если нужно, с учётом гистерезиса/границ уже учтённых сервисом
        old_price = float(product.price) if product.price is not None else None
        new_price = float(result.target_price)
        if old_price != new_price:
            product.price = new_price
            db.commit()
            applied = True
            audit_logger.log_data_change(
                user_id=current_user.id,
                action="repricing_apply",
                resource_type="product",
                resource_id=str(product.id),
                changes={"price": {"old": old_price, "new": new_price}},
            )

    return RepricingTickOut(
        current_price=result.current_price,
        target_price=result.target_price,
        best_competitor=result.best_competitor,
        reason=result.reason,
        applied=applied,
    )
