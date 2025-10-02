# app/api/v1/products.py
from __future__ import annotations
"""
Products router (enterprise-grade) for catalog management.

Ключевые особенности:
- Company scoping и soft-delete: фильтры через is_deleted.is_(False).
- Пагинация, фильтры, сортировка, ETag/If-None-Match для GET по id.
- RBAC: require_manager для мутаций, текущий пользователь для чтения.
- Идемпотентность для POST/PUT/DELETE/импорта/загрузки изображений.
- Аудит действий.
- Аккуратная работа с Cloudinary (замена изображения с удалением старого).
- Импорт/экспорт в Excel с предсказуемыми ошибками и аудитом.
"""

import hashlib
from typing import Optional

from fastapi import (
    APIRouter,
    Depends,
    File,
    Header,
    HTTPException,
    Request,
    Response,
    UploadFile,
    status,
)
from sqlalchemy import and_, or_, select, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from pydantic import BaseModel, Field, field_validator

# --- core deps / db / security / logging / errors ---
try:
    from app.core.database import get_db  # предпочтительно
except Exception:  # pragma: no cover
    from app.core.db import get_db  # fallback

from app.core.deps import (
    api_rate_limit_dep,
    ensure_idempotency,
    set_idempotency_result,
    get_pagination,
    Pagination,
    get_client_info,
    # require_scopes,  # при необходимости подключите скоупы
)
from app.core.security import get_current_user, require_manager
from app.core.exceptions import bad_request, conflict, not_found, server_error
from app.core.logging import audit_logger

# --- models & schemas ---
from app.models import Product, ProductStock, User
from app.schemas import ProductCreate, ProductResponse, ProductUpdate

# --- services & utils ---
from app.services.cloudinary_service import CloudinaryService
from app.utils.excel import export_products_to_excel, import_products_from_excel


router = APIRouter(
    prefix="/products",
    tags=["products"],
    dependencies=[Depends(api_rate_limit_dep)],
)


# ---------------------------------------------------------------------
# DTOs / Filters / Sorting
# ---------------------------------------------------------------------
class PaginationParams(BaseModel):
    page: int = Field(1, ge=1)
    size: int = Field(20, ge=1, le=100)

    def offset(self) -> int:
        return (self.page - 1) * self.size


class ProductFilter(BaseModel):
    search: str | None = None
    category: str | None = None
    brand: str | None = None
    is_active: bool | None = None
    is_hidden: bool | None = None
    min_price: float | None = Field(default=None, ge=0)
    max_price: float | None = Field(default=None, ge=0)

    @field_validator("max_price")
    @classmethod
    def _check_price_bounds(cls, v, info):
        min_p = info.data.get("min_price")
        if v is not None and min_p is not None and v < min_p:
            raise ValueError("max_price must be >= min_price")
        return v


class SortParams(BaseModel):
    """Простая сортировка по разрешённым полям."""
    sort_by: str | None = Field(default=None)  # name|price|created_at
    order: str | None = Field(default="asc")   # asc|desc

    def apply(self, stmt, model: type[Product]):
        if not self.sort_by:
            return stmt.order_by(model.name.asc(), model.id.asc())
        mapping = {
            "name": model.name,
            "price": model.price,
            "created_at": getattr(model, "created_at", model.id),  # fallback
        }
        col = mapping.get(self.sort_by, model.name)
        if (self.order or "asc").lower() == "desc":
            return stmt.order_by(col.desc(), model.id.desc())
        return stmt.order_by(col.asc(), model.id.asc())


# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------
def _company_active_filter(model, company_id: int):
    """Удобный фильтр по компании с учётом soft-delete."""
    cond = [model.company_id == company_id]
    if hasattr(model, "is_deleted"):
        cond.append(model.is_deleted.is_(False))
    return and_(*cond)


def _product_etag_src(p: Product) -> str:
    # Минимально стабильный набор полей для ETag. При необходимости расширьте.
    price = float(p.price or 0)
    return f"{p.id}|{p.name}|{p.sku}|{price}|{int(p.is_active)}|{int(p.is_hidden)}"


# ---------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------
@router.get(
    "",
    response_model=list[ProductResponse],
    summary="Список товаров (фильтры, пагинация, сортировка)",
)
async def get_products(
    pagination: PaginationParams = Depends(),
    filter_params: ProductFilter = Depends(),
    sort_params: SortParams = Depends(),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    stmt = (
        select(Product)
        .where(_company_active_filter(Product, current_user.company_id))
        .options(selectinload(Product.company))
        .offset(pagination.offset())
        .limit(pagination.size)
    )

    # Фильтры
    if filter_params.search:
        s = f"%{filter_params.search}%"
        stmt = stmt.where(
            or_(
                Product.name.ilike(s),
                Product.sku.ilike(s),
                Product.description.ilike(s),
            )
        )
    if filter_params.category:
        stmt = stmt.where(Product.category == filter_params.category)
    if filter_params.brand:
        stmt = stmt.where(Product.brand == filter_params.brand)
    if filter_params.is_active is not None:
        stmt = stmt.where(Product.is_active == filter_params.is_active)
    if filter_params.is_hidden is not None:
        stmt = stmt.where(Product.is_hidden == filter_params.is_hidden)
    if filter_params.min_price is not None:
        stmt = stmt.where(Product.price >= filter_params.min_price)
    if filter_params.max_price is not None:
        stmt = stmt.where(Product.price <= filter_params.max_price)

    # Сортировка
    stmt = sort_params.apply(stmt, Product)

    res = await db.execute(stmt)
    return res.scalars().all()


@router.post(
    "",
    response_model=ProductResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Создать товар",
    dependencies=[Depends(require_manager), Depends(ensure_idempotency)],
)
async def create_product(
    product_data: ProductCreate,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # SKU unique в рамках компании
    exists = await db.execute(
        select(func.count(Product.id)).where(
            and_(
                Product.company_id == current_user.company_id,
                Product.sku == product_data.sku,
                Product.is_deleted.is_(False),
            )
        )
    )
    if exists.scalar_one() > 0:
        raise conflict("Product with this SKU already exists")

    product = Product(company_id=current_user.company_id, **product_data.model_dump())
    db.add(product)
    try:
        await db.commit()
    except IntegrityError as e:
        await db.rollback()
        # На случай уникальных индексов имени или SKU в базе
        raise conflict("Product violates a unique constraint") from e

    await db.refresh(product)

    # Аудит
    audit_logger.log_data_change(
        user_id=current_user.id,
        action="product_create",
        resource_type="product",
        resource_id=str(product.id),
        changes=product_data.model_dump(),
    )

    # Идемпотентность
    if hasattr(request.state, "idempotency_key"):
        await set_idempotency_result(request.state.idempotency_key, status.HTTP_201_CREATED)

    return product


@router.get(
    "/{product_id}",
    response_model=ProductResponse,
    summary="Получить товар по ID (с ETag)",
)
async def get_product(
    product_id: int,
    response: Response,
    if_none_match: Optional[str] = Header(default=None, convert_underscores=False),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    res = await db.execute(
        select(Product)
        .where(
            and_(
                Product.id == product_id,
                Product.company_id == current_user.company_id,
                Product.is_deleted.is_(False),
            )
        )
        .options(selectinload(Product.company))
    )
    product = res.scalar_one_or_none()
    if not product:
        raise not_found("Product not found")

    # ETag
    etag = hashlib.sha256(_product_etag_src(product).encode("utf-8")).hexdigest()
    response.headers["ETag"] = etag
    if if_none_match and if_none_match.strip('"') == etag:
        # 304 без тела
        return Response(status_code=status.HTTP_304_NOT_MODIFIED)

    return product


@router.put(
    "/{product_id}",
    response_model=ProductResponse,
    summary="Обновить товар",
    dependencies=[Depends(require_manager), Depends(ensure_idempotency)],
)
async def update_product(
    product_id: int,
    product_data: ProductUpdate,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    res = await db.execute(
        select(Product).where(
            and_(
                Product.id == product_id,
                Product.company_id == current_user.company_id,
                Product.is_deleted.is_(False),
            )
        )
    )
    product = res.scalar_one_or_none()
    if not product:
        raise not_found("Product not found")

    # Старые значения для аудита (минимально необходимые)
    old_values = {
        "name": product.name,
        "price": float(product.price or 0),
        "is_active": product.is_active,
        "is_hidden": product.is_hidden,
    }

    # Обновление
    updates = product_data.model_dump(exclude_unset=True)
    # Защитимся от смены SKU на уже занятый
    new_sku = updates.get("sku")
    if new_sku and new_sku != product.sku:
        dup = await db.execute(
            select(func.count(Product.id)).where(
                and_(
                    Product.company_id == current_user.company_id,
                    Product.sku == new_sku,
                    Product.id != product_id,
                    Product.is_deleted.is_(False),
                )
            )
        )
        if dup.scalar_one() > 0:
            raise conflict("Another product with this SKU already exists")

    for field, value in updates.items():
        setattr(product, field, value)

    try:
        await db.commit()
    except IntegrityError as e:
        await db.rollback()
        raise conflict("Product update violates a constraint") from e

    await db.refresh(product)

    # Аудит
    audit_logger.log_data_change(
        user_id=current_user.id,
        action="product_update",
        resource_type="product",
        resource_id=str(product.id),
        changes={"old": old_values, "new": updates},
    )

    # Идемпотентность
    if hasattr(request.state, "idempotency_key"):
        await set_idempotency_result(request.state.idempotency_key, status.HTTP_200_OK)

    return product


@router.delete(
    "/{product_id}",
    summary="Удалить товар (soft delete)",
    dependencies=[Depends(require_manager), Depends(ensure_idempotency)],
)
async def delete_product(
    product_id: int,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    res = await db.execute(
        select(Product).where(
            and_(
                Product.id == product_id,
                Product.company_id == current_user.company_id,
                Product.is_deleted.is_(False),
            )
        )
    )
    product = res.scalar_one_or_none()
    if not product:
        raise not_found("Product not found")

    # Soft delete
    if hasattr(product, "soft_delete"):
        product.soft_delete()
    else:
        product.is_deleted = True

    await db.commit()

    audit_logger.log_data_change(
        user_id=current_user.id,
        action="product_delete",
        resource_type="product",
        resource_id=str(product.id),
        changes={"deleted": True},
    )

    if hasattr(request.state, "idempotency_key"):
        await set_idempotency_result(request.state.idempotency_key, status.HTTP_200_OK)

    return {"message": "Product deleted successfully"}


# ------------------------ Images ------------------------
@router.post(
    "/{product_id}/upload-image",
    summary="Загрузить изображение товара в Cloudinary",
    dependencies=[Depends(require_manager), Depends(ensure_idempotency)],
)
async def upload_product_image(
    product_id: int,
    file: UploadFile = File(...),
    request: Request = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # Проверяем товар
    res = await db.execute(
        select(Product).where(
            and_(
                Product.id == product_id,
                Product.company_id == current_user.company_id,
                Product.is_deleted.is_(False),
            )
        )
    )
    product = res.scalar_one_or_none()
    if not product:
        raise not_found("Product not found")

    # Валидация файла
    if not file.content_type or not file.content_type.startswith("image/"):
        raise bad_request("File must be an image")
    # Дополнительно можно ограничить размер, набор расширений и т.п.

    cloudinary = CloudinaryService()
    try:
        uploaded = await cloudinary.upload_image(
            file=file, folder=f"products/{current_user.company_id}"
        )
    except Exception as e:
        raise server_error(f"Upload failed: {e!s}")

    if not uploaded or "secure_url" not in uploaded:
        raise server_error("Cloud upload returned unexpected response")

    old_public_id = product.image_public_id
    product.image_url = uploaded["secure_url"]
    product.image_public_id = uploaded.get("public_id")
    await db.commit()

    # Удаляем старое изображение (best-effort)
    if old_public_id:
        try:
            await cloudinary.delete_image(old_public_id)
        except Exception:
            # Не валим поток: логирование само произойдёт в сервисе/обработчике
            pass

    audit_logger.log_data_change(
        user_id=current_user.id,
        action="product_image_upload",
        resource_type="product",
        resource_id=str(product.id),
        changes={"image_public_id": product.image_public_id, "image_url": product.image_url},
    )

    if request is not None and hasattr(request.state, "idempotency_key"):
        await set_idempotency_result(request.state.idempotency_key, status.HTTP_200_OK)

    return {"message": "Image uploaded successfully", "image_url": product.image_url}


# ------------------------ Import / Export ------------------------
@router.post(
    "/import",
    summary="Импорт товаров из Excel",
    dependencies=[Depends(require_manager), Depends(ensure_idempotency)],
)
async def import_products(
    file: UploadFile = File(...),
    request: Request = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    filename = (file.filename or "").lower()
    if not (filename.endswith(".xlsx") or filename.endswith(".xls")):
        raise bad_request("File must be Excel format (.xlsx or .xls)")

    try:
        import_result = await import_products_from_excel(
            file=file, company_id=current_user.company_id, db=db
        )
        audit_logger.log_data_change(
            user_id=current_user.id,
            action="product_import",
            resource_type="product",
            resource_id="*",
            changes=import_result,
        )
        if request is not None and hasattr(request.state, "idempotency_key"):
            await set_idempotency_result(request.state.idempotency_key, status.HTTP_200_OK)
        return import_result
    except HTTPException:
        raise
    except Exception as e:
        raise bad_request(f"Import failed: {e!s}")


@router.get(
    "/export/excel",
    summary="Экспорт товаров в Excel",
    dependencies=[Depends(require_manager)],
)
async def export_products(
    filter_params: ProductFilter = Depends(),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        stmt = select(Product).where(
            and_(
                Product.company_id == current_user.company_id,
                Product.is_deleted.is_(False),
            )
        )

        # Минимальный набор фильтров (как в get_products)
        if filter_params.category:
            stmt = stmt.where(Product.category == filter_params.category)
        if filter_params.is_active is not None:
            stmt = stmt.where(Product.is_active == filter_params.is_active)

        res = await db.execute(stmt)
        products = res.scalars().all()

        # Генерим Excel (возврат пути/ссылки - как в вашем util)
        file_path = await export_products_to_excel(products)

        audit_logger.log_data_change(
            user_id=current_user.id,
            action="product_export",
            resource_type="product",
            resource_id="*",
            changes={"products_count": len(products), "file": file_path},
        )
        return {"download_url": file_path}
    except HTTPException:
        raise
    except Exception as e:
        raise server_error(f"Export failed: {e!s}")


# ------------------------ Stock summary by product ------------------------
@router.get(
    "/{product_id}/stock",
    summary="Остатки товара по всем складам",
)
async def get_product_stock(
    product_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # Проверяем, что товар принадлежит компании и не удалён
    res = await db.execute(
        select(Product).where(
            and_(
                Product.id == product_id,
                Product.company_id == current_user.company_id,
                Product.is_deleted.is_(False),
            )
        )
    )
    product = res.scalar_one_or_none()
    if not product:
        raise not_found("Product not found")

    # Берём остатки
    res = await db.execute(
        select(ProductStock).where(ProductStock.product_id == product_id)
    )
    stocks = res.scalars().all()

    return {
        "product_id": product_id,
        "total_stock": sum(s.quantity for s in stocks),
        "available_stock": sum(getattr(s, "available_quantity", 0) for s in stocks),
        "warehouses": [
            {
                "warehouse_id": s.warehouse_id,
                "quantity": s.quantity,
                "reserved_quantity": getattr(s, "reserved_quantity", 0),
                "available_quantity": getattr(s, "available_quantity", s.quantity),
            }
            for s in stocks
        ],
    }
