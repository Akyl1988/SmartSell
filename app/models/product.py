# app/models/product.py
"""
Product domain models: Category, Product, ProductVariant.

Особенности:
- PostgreSQL-friendly server_default: text('true'/'false'), text('0')
- UTC naive timestamps + onupdate=datetime.utcnow
- Optimistic locking (version column)
- Денежные поля: Numeric(14, 2)
- JSON в текстовых колонках (gallery/attributes/extra) с явной (де)сериализацией
- Soft delete (deleted_at) + индексы под частые запросы
- Поиск/экспорт/импорт, каскадный soft-delete/restore
- Bulk upsert вариантов с аудитом и файловым логом

Дополнения:
- Поля под предзаказы и демпинг (repriced_at, price_updated_at, preorder_lead_days, preorder_show_zero_stock)
- Поле extra (Text JSON) — гибкая конфигурация: repricing, preorder и т.п.
- Хелперы для безопасной работы с extra и конфигом репрайсинга
- Методы enable_preorder/disable_preorder/auto_preorder_on_depletion
- Методы управления ценой c учётом min/max и sale_price
"""

from __future__ import annotations

import csv
import io
import json
import logging
import os
from collections.abc import Callable, Iterable
from datetime import datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Any, ClassVar, Optional

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    and_,
    func,
    or_,
    select,
    text,
)
from sqlalchemy.ext.hybrid import hybrid_property
from sqlalchemy.orm import (
    Mapped,
    Session,
    declarative_mixin,
    mapped_column,
    relationship,
    validates,
)

# ---------------------------------------------------------------------
# Fallback «мягкого» __init__, как в user.py — спасает при ранних циклах импорта
# ---------------------------------------------------------------------
try:
    from app.models.base import LenientInitMixin  # type: ignore
except Exception:  # pragma: no cover

    class LenientInitMixin:  # type: ignore
        def __init__(self, **kwargs):
            try:
                super().__init__()
            except Exception:
                pass
            for k, v in (kwargs or {}).items():
                try:
                    setattr(self, k, v)
                except Exception:
                    pass


from app.models.base import BaseModel

if TYPE_CHECKING:
    from app.models.warehouse import ProductStock, StockMovement  # pragma: no cover

    try:
        from app.models.audit import AuditLog  # pragma: no cover
    except Exception:  # pragma: no cover
        from app.models.audit_log import AuditLog  # type: ignore


# =============================================================================
# Логирование для bulk upsert
# =============================================================================

BULK_UPSERT_LOG_PATH = os.getenv("BULK_UPSERT_LOG_PATH", "logs/variant_bulk_upsert.log")
_BULK_LOGGER: Optional[logging.Logger] = None


def _get_bulk_logger() -> logging.Logger:
    global _BULK_LOGGER
    if _BULK_LOGGER is not None:
        return _BULK_LOGGER
    logger = logging.getLogger("product.bulk_upsert")
    logger.setLevel(logging.INFO)
    try:
        os.makedirs(os.path.dirname(BULK_UPSERT_LOG_PATH), exist_ok=True)
        fh = logging.FileHandler(BULK_UPSERT_LOG_PATH, encoding="utf-8")
        fh.setLevel(logging.INFO)
        fmt = logging.Formatter("%(asctime)s | %(levelname)s | %(message)s", "%Y-%m-%d %H:%M:%S")
        fh.setFormatter(fmt)
        logger.propagate = False
        if not any(
            isinstance(h, logging.FileHandler)
            and getattr(h, "baseFilename", "") == os.path.abspath(BULK_UPSERT_LOG_PATH)
            for h in logger.handlers
        ):
            logger.addHandler(fh)
    except Exception:
        pass
    _BULK_LOGGER = logger
    return logger


# =============================================================================
# JSON / Serialization utils
# =============================================================================


def _jsonify_value(v: Any) -> Any:
    if isinstance(v, Decimal):
        return format(v, "f")
    if isinstance(v, datetime):
        return v.replace(microsecond=0).isoformat()
    return v


def _row_to_dict(obj: Any, json_safe: bool = False) -> dict[str, Any]:
    data = {c.name: getattr(obj, c.name) for c in obj.__table__.columns}
    if json_safe:
        data = {k: _jsonify_value(v) for k, v in data.items()}
    return data


def _ensure_json_dict(v: Any, *, allow_none: bool = True) -> dict[str, Any]:
    if v is None and allow_none:
        return {}
    if isinstance(v, dict):
        json.dumps(v)
        return v
    if isinstance(v, str):
        try:
            parsed = json.loads(v or "{}")
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            pass
    raise ValueError("Value must be dict or JSON string of object")


def _ensure_json_list_of_str(v: Any, *, allow_none: bool = True) -> list[str]:
    if v is None and allow_none:
        return []
    if isinstance(v, list) and all(isinstance(i, str) for i in v):
        return v
    if isinstance(v, str):
        try:
            parsed = json.loads(v or "[]")
            if isinstance(parsed, list) and all(isinstance(i, str) for i in parsed):
                return parsed
        except Exception:
            pass
    raise ValueError("Value must be list[str] or JSON string of list[str]")


# =============================================================================
# Optimistic Locking
# =============================================================================
@declarative_mixin
class VersionedMixin:
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    __mapper_args__ = {"version_id_col": version}


# =============================================================================
# Category
# =============================================================================
class Category(LenientInitMixin, BaseModel):
    __tablename__ = "categories"
    __allow_unmapped__ = True

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    description: Mapped[Optional[str]] = mapped_column(Text)

    parent_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("categories.id", ondelete="SET NULL"), index=True
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    sort_order: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )

    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime, index=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    parent: Mapped[Optional[Category]] = relationship(
        "Category", remote_side="Category.id", back_populates="children"
    )
    children: Mapped[list[Category]] = relationship(
        "Category", back_populates="parent", cascade="save-update, merge", single_parent=True
    )
    products: Mapped[list[Product]] = relationship("Product", back_populates="category")

    __table_args__ = (
        Index("ix_category_parent_active", "parent_id", "is_active"),
        CheckConstraint("sort_order >= 0", name="ck_category_sort_nonneg"),
        {"extend_existing": True},
    )

    @validates("slug")
    def _norm_slug(self, _k: str, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        vv = v.strip().lower()
        if not vv or " " in vv:
            raise ValueError("slug must be non-empty and without spaces")
        return vv

    @validates("name")
    def _norm_name(self, _k: str, v: Optional[str]) -> str:
        vv = v.strip() if v else ""
        if not vv:
            raise ValueError("name cannot be empty")
        return vv

    @validates("parent_id")
    def _validate_parent(self, _k: str, v: Optional[int]) -> Optional[int]:
        if v is not None and v == getattr(self, "id", None):
            raise ValueError("Category cannot be parent of itself")
        return v

    def __repr__(self) -> str:
        return f"<Category(id={self.id}, name='{self.name}')>"

    def to_dict(self, with_products: bool = False, json_safe: bool = False) -> dict[str, Any]:
        data = _row_to_dict(self, json_safe=json_safe)
        if with_products:
            data["products"] = [p.to_dict(json_safe=json_safe) for p in self.products]
        return data

    @property
    def is_root(self) -> bool:
        return self.parent_id is None

    def get_full_path(self) -> str:
        path: list[str] = []
        cur: Optional[Category] = self
        while cur:
            path.append(cur.slug)
            cur = cur.parent
        return "/".join(reversed(path))

    def get_active_children(self) -> list[Category]:
        return [child for child in self.children if child.is_active and not child.deleted_at]

    def get_all_nested_products(self) -> list[Product]:
        products = [p for p in self.products if not p.deleted_at]
        for child in self.get_active_children():
            products.extend(child.get_all_nested_products())
        return products

    def activate(self) -> None:
        self.is_active = True

    def deactivate(self) -> None:
        self.is_active = False

    def soft_delete(self) -> None:
        self.deleted_at = datetime.utcnow()

    def restore(self) -> None:
        self.deleted_at = None

    def set_parent(self, session: Session, new_parent_id: Optional[int]) -> None:
        if new_parent_id is None:
            self.parent_id = None
            session.flush()
            return
        if new_parent_id == self.id:
            raise ValueError("Category cannot be parent of itself")

        def _is_descendant(candidate_id: int, current: Category) -> bool:
            for ch in current.children:
                if ch.id == candidate_id or _is_descendant(candidate_id, ch):
                    return True
            return False

        if self.id is not None and _is_descendant(new_parent_id, self):
            raise ValueError("Cannot assign a descendant as parent (cycle)")

        self.parent_id = new_parent_id
        session.flush()

    @classmethod
    def create(cls, session: Session, name: str, slug: str, **kwargs) -> Category:
        obj = cls(name=name, slug=slug, **kwargs)
        session.add(obj)
        session.commit()
        return obj

    @classmethod
    def get_by_slug(cls, session: Session, slug: str) -> Optional[Category]:
        return session.query(cls).filter_by(slug=slug).first()

    @classmethod
    def get_all(cls, session: Session) -> list[Category]:
        return session.query(cls).all()

    def update(self, session: Session, **fields) -> Category:
        for k, v in fields.items():
            setattr(self, k, v)
        session.commit()
        return self

    def delete(self, session: Session) -> None:
        session.delete(self)
        session.commit()


# =============================================================================
# Product
# =============================================================================
class Product(LenientInitMixin, VersionedMixin, BaseModel):
    __tablename__ = "products"
    __allow_unmapped__ = True

    emit_audit_async_hook: ClassVar[
        Optional[Callable[[Optional[Session], Any, str, dict[str, Any]], None]]
    ] = None

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    company_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), nullable=True, index=True
    )
    name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, index=True)
    slug: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, index=True)
    sku: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, index=True)

    description: Mapped[Optional[str]] = mapped_column(Text)
    short_description: Mapped[Optional[str]] = mapped_column(String(500))

    price: Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 2), index=True)
    min_price: Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 2))
    max_price: Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 2))
    cost_price: Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 2))
    sale_price: Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 2))

    stock_quantity: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    reserved_quantity: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )

    min_stock_level: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    max_stock_level: Mapped[Optional[int]] = mapped_column(Integer)

    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=text("true"), index=True
    )
    is_featured: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false"), index=True
    )

    # --- Предзаказы ---
    is_preorder_enabled: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false")
    )
    preorder_until: Mapped[Optional[int]] = mapped_column(Integer)  # epoch seconds, если задано
    preorder_deposit: Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 2))
    preorder_note: Mapped[Optional[str]] = mapped_column(String(500))
    preorder_lead_days: Mapped[Optional[int]] = mapped_column(Integer)  # N дней до поставки
    preorder_show_zero_stock: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=text("true")
    )

    # --- Демпинг / Рынок ---
    enable_price_dumping: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false")
    )
    exclude_friendly_stores: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=text("true")
    )
    repriced_at: Mapped[Optional[datetime]] = mapped_column(DateTime, index=True)
    price_updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime, index=True)

    # --- Мета/медиа/категории ---
    category_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("categories.id", ondelete="SET NULL"), index=True
    )
    image_url: Mapped[Optional[str]] = mapped_column(String(500))
    image_public_id: Mapped[Optional[str]] = mapped_column(String(255))
    gallery_urls: Mapped[Optional[str]] = mapped_column(Text)  # JSON array (text)

    meta_title: Mapped[Optional[str]] = mapped_column(String(255))
    meta_description: Mapped[Optional[str]] = mapped_column(String(500))
    meta_keywords: Mapped[Optional[str]] = mapped_column(String(500))

    # --- Kaspi ---
    kaspi_product_id: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    kaspi_status: Mapped[Optional[str]] = mapped_column(String(32), index=True)

    # --- Расширяемое JSON-хранилище ---
    extra: Mapped[Optional[str]] = mapped_column(Text)  # свободный JSON (repricing/preorder и т.д.)

    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime, index=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    # relationships
    company: Mapped[Optional[Any]] = relationship("Company", back_populates="products")
    category: Mapped[Optional[Category]] = relationship("Category", back_populates="products")
    variants: Mapped[list[ProductVariant]] = relationship(
        "ProductVariant", back_populates="product", cascade="all, delete-orphan"
    )
    stocks: Mapped[list[ProductStock]] = relationship(
        "ProductStock", back_populates="product", cascade="all, delete-orphan"
    )
    stock_movements: Mapped[list[StockMovement]] = relationship(
        "StockMovement", back_populates="product", cascade="all, delete-orphan"
    )
    audit_logs: Mapped[list[AuditLog]] = relationship(
        "AuditLog",
        back_populates="product",
        cascade="all, delete-orphan",
        foreign_keys="AuditLog.product_id",
        lazy="selectin",
    )
    order_items: Mapped[list[Any]] = relationship(
        "OrderItem", back_populates="product", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("company_id", "sku", name="uq_product_company_sku"),
        UniqueConstraint("company_id", "slug", name="uq_product_company_slug"),
        Index("ix_product_company_active", "company_id", "is_active"),
        Index(
            "ix_product_company_category_active_del",
            "company_id",
            "category_id",
            "is_active",
            "deleted_at",
        ),
        Index("ix_product_company_featured", "company_id", "is_featured"),
        Index("ix_product_price_active", "company_id", "price", "is_active"),
        Index("ix_product_search_name", "company_id", "name"),
        Index("ix_product_search_name_sku", "company_id", "name", "sku"),
        Index("ix_kaspi_company_status", "company_id", "kaspi_status"),
        CheckConstraint("(price IS NULL OR price >= 0)", name="ck_prod_price_nonneg"),
        CheckConstraint("(min_price IS NULL OR min_price >= 0)", name="ck_prod_min_price_nonneg"),
        CheckConstraint("(max_price IS NULL OR max_price >= 0)", name="ck_prod_max_price_nonneg"),
        CheckConstraint(
            "(sale_price IS NULL OR sale_price >= 0)", name="ck_prod_sale_price_nonneg"
        ),
        CheckConstraint("stock_quantity >= 0", name="ck_prod_stock_nonneg"),
        CheckConstraint("reserved_quantity >= 0", name="ck_prod_reserved_nonneg"),
        CheckConstraint("min_stock_level >= 0", name="ck_prod_min_stock_nonneg"),
        CheckConstraint(
            "(max_stock_level IS NULL OR max_stock_level >= min_stock_level)",
            name="ck_prod_stock_bounds",
        ),
        CheckConstraint(
            "(min_price IS NULL OR price IS NULL OR price >= min_price)",
            name="ck_prod_price_ge_min",
        ),
        CheckConstraint(
            "(max_price IS NULL OR price IS NULL OR price <= max_price)",
            name="ck_prod_price_le_max",
        ),
        CheckConstraint(
            "(sale_price IS NULL OR price IS NULL OR sale_price <= price)",
            name="ck_prod_sale_le_price",
        ),
        CheckConstraint(
            "(sale_price IS NULL OR min_price IS NULL OR sale_price >= min_price)",
            name="ck_prod_sale_ge_min",
        ),
        CheckConstraint(
            "(preorder_deposit IS NULL OR price IS NULL OR preorder_deposit <= price)",
            name="ck_prod_preorder_deposit_le_price",
        ),
        CheckConstraint(
            "(enable_price_dumping OR cost_price IS NULL OR price IS NULL OR price >= cost_price)",
            name="ck_prod_price_ge_cost_when_not_dumping",
        ),
        CheckConstraint(
            "((NOT is_preorder_enabled) OR (preorder_until IS NULL OR preorder_until >= 0))",
            name="ck_prod_preorder_until_nonneg",
        ),
        CheckConstraint("reserved_quantity <= stock_quantity", name="ck_prod_reserved_le_stock"),
        CheckConstraint(
            "(preorder_lead_days IS NULL OR preorder_lead_days >= 0)",
            name="ck_prod_preorder_lead_days_nonneg",
        ),
        {"extend_existing": True},
    )

    @validates("name", "slug", "sku")
    def _norm_required(self, key: str, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        vv = v.strip()
        return vv.lower() if key in ("slug", "sku") else vv

    @validates("price", "cost_price", "sale_price", "min_price", "max_price", "preorder_deposit")
    def _validate_money(self, key: str, value: Optional[Decimal]) -> Optional[Decimal]:
        if value is not None and value < 0:
            raise ValueError(f"{key} cannot be negative")
        if (
            key == "preorder_deposit"
            and value is not None
            and self.price is not None
            and value > self.price
        ):
            raise ValueError("preorder_deposit cannot exceed price")
        return value

    @validates(
        "stock_quantity",
        "reserved_quantity",
        "min_stock_level",
        "max_stock_level",
        "preorder_lead_days",
    )
    def _validate_stock(self, key: str, value: Optional[int]) -> Optional[int]:
        if value is not None and value < 0:
            raise ValueError(f"{key} cannot be negative")
        return value

    def __repr__(self) -> str:
        return f"<Product(id={self.id}, name='{self.name}', sku='{self.sku}')>"

    # Derived / labels
    @property
    def display_label(self) -> str:
        sku = f"[{self.sku}]" if self.sku else ""
        return f"{self.name or ''} {sku}".strip()

    @property
    def is_archived(self) -> bool:
        return self.deleted_at is not None

    @property
    def search_text(self) -> str:
        parts = [
            self.name or "",
            self.sku or "",
            self.slug or "",
            self.meta_title or "",
            self.meta_keywords or "",
        ]
        return " ".join(parts).strip().lower()

    @property
    def full_search_text(self) -> str:
        path = self.category.get_full_path() if self.category else ""
        parts = [
            self.search_text,
            path or "",
            self.meta_description or "",
            self.kaspi_product_id or "",
            self.kaspi_status or "",
        ]
        return " ".join(p for p in parts if p).strip().lower()

    # Serialize
    def to_dict(
        self,
        with_variants: bool = False,
        with_stocks: bool = False,
        with_audit: bool = False,
        json_safe: bool = False,
    ) -> dict[str, Any]:
        data = _row_to_dict(self, json_safe=json_safe)
        gallery_raw = data.get("gallery_urls")
        if gallery_raw:
            try:
                data["gallery_urls"] = json.loads(gallery_raw)
            except Exception:
                data["gallery_urls"] = []
        extra_raw = data.get("extra")
        if extra_raw:
            try:
                data["extra"] = json.loads(extra_raw)
            except Exception:
                data["extra"] = {}
        if with_variants:
            data["variants"] = [v.to_dict() for v in self.variants]
        if with_stocks:
            data["stocks"] = [s.to_dict() for s in self.stocks]
        if with_audit:
            data["audit_logs"] = [a.to_dict(json_safe=json_safe) for a in self.audit_logs]
        data["display_label"] = self.display_label
        data["is_archived"] = self.is_archived
        data["search_text"] = self.search_text
        data["full_search_text"] = self.full_search_text
        return data

    # ---- Extra JSON helpers ----
    def get_extra(self) -> dict[str, Any]:
        return _ensure_json_dict(self.extra, allow_none=True)

    def set_extra(self, payload: dict[str, Any]) -> None:
        clean = _ensure_json_dict(payload, allow_none=True)
        self.extra = json.dumps(clean)

    def update_extra_path(self, key: str, value: Any) -> None:
        data = self.get_extra()
        data[key] = value
        self.extra = json.dumps(data)

    # ---- Repricing config in extra ----
    def get_repricing_config(self) -> dict[str, Any]:
        data = self.get_extra()
        cfg = data.get("repricing") or data.get("repricing_config") or data.get("demping") or {}
        return _ensure_json_dict(cfg, allow_none=True)

    def set_repricing_config(self, cfg: dict[str, Any]) -> None:
        data = self.get_extra()
        data["repricing"] = _ensure_json_dict(cfg, allow_none=True)
        self.extra = json.dumps(data)

    # Search
    @classmethod
    def search(
        cls, session: Session, company_id: Optional[int], q: str, limit: int = 50
    ) -> list[Product]:
        query = session.query(cls).filter(cls.deleted_at.is_(None))
        if company_id is not None:
            query = query.filter(cls.company_id == company_id)
        if q:
            like = f"%{q.strip().lower()}%"
            query = query.filter(or_(cls.name.ilike(like), cls.sku.ilike(like)))
        return query.order_by(cls.name.asc()).limit(limit).all()

    @classmethod
    def search_advanced(
        cls,
        session: Session,
        *,
        company_id: Optional[int],
        q: Optional[str] = None,
        category_id: Optional[int] = None,
        only_active: bool = True,
        limit: int = 100,
    ) -> list[Product]:
        query = session.query(cls)
        if company_id is not None:
            query = query.filter(cls.company_id == company_id)
        if only_active:
            query = query.filter(and_(cls.is_active.is_(True), cls.deleted_at.is_(None)))
        if category_id is not None:
            query = query.filter(cls.category_id == category_id)
        if q:
            like = f"%{(q or '').strip().lower()}%"
            query = query.filter(
                or_(cls.name.ilike(like), cls.sku.ilike(like), cls.slug.ilike(like))
            )
        return query.order_by(cls.updated_at.desc()).limit(limit).all()

    # CRUD
    @classmethod
    def create(
        cls,
        session: Session,
        name: str,
        sku: str,
        slug: str,
        company_id: Optional[int] = None,
        **kwargs,
    ) -> Product:
        obj = cls(name=name, sku=sku, slug=slug, company_id=company_id, **kwargs)
        session.add(obj)
        session.commit()
        return obj

    @classmethod
    def get_by_sku(
        cls, session: Session, sku: str, company_id: Optional[int] = None
    ) -> Optional[Product]:
        query = session.query(cls).filter_by(sku=sku)
        if company_id is not None:
            query = query.filter_by(company_id=company_id)
        return query.first()

    @classmethod
    def get_by_slug(
        cls, session: Session, slug: str, company_id: Optional[int] = None
    ) -> Optional[Product]:
        query = session.query(cls).filter_by(slug=slug)
        if company_id is not None:
            query = query.filter_by(company_id=company_id)
        return query.first()

    @classmethod
    def get_all(cls, session: Session, company_id: Optional[int] = None) -> list[Product]:
        query = session.query(cls)
        if company_id is not None:
            query = query.filter_by(company_id=company_id)
        return query.all()

    def update(self, session: Session, **fields) -> Product:
        for k, v in fields.items():
            setattr(self, k, v)
        session.commit()
        return self

    def delete(self, session: Session) -> None:
        session.delete(self)
        session.commit()

    # Soft-delete / restore (cascade)
    def soft_delete(
        self,
        session: Optional[Session] = None,
        *,
        cascade: bool = True,
        actor_id: Optional[int] = None,
    ) -> None:
        self.deleted_at = datetime.utcnow()
        if cascade:
            now = self.deleted_at
            for v in self.variants:
                v.deleted_at = now
            for s in self.stocks:
                if hasattr(s, "deleted_at"):
                    s.deleted_at = now
        self.emit_audit("soft_delete", {"cascade": cascade}, actor_id=actor_id)
        if session:
            session.flush()

    def restore(
        self,
        session: Optional[Session] = None,
        *,
        cascade: bool = True,
        actor_id: Optional[int] = None,
    ) -> None:
        self.deleted_at = None
        if cascade:
            for v in self.variants:
                v.deleted_at = None
            for s in self.stocks:
                if hasattr(s, "deleted_at"):
                    s.deleted_at = None
        self.emit_audit("restore", {"cascade": cascade}, actor_id=actor_id)
        if session:
            session.flush()

    # Optimistic update
    def update_with_version(self, session: Session, expected_version: int, **fields) -> Product:
        from sqlalchemy import update as sa_update  # локально

        stmt = (
            sa_update(Product)
            .where(Product.id == self.id, Product.version == expected_version)
            .values(**fields, version=Product.version + 1, updated_at=datetime.utcnow())
            .execution_options(synchronize_session="fetch")
        )
        res = session.execute(stmt)
        if res.rowcount == 0:
            raise ValueError("Version conflict")
        session.flush()
        session.refresh(self)
        return self

    # Meta / Audit
    def set_metadata(
        self,
        title: Optional[str] = None,
        description: Optional[str] = None,
        keywords: Optional[str] = None,
    ) -> None:
        if title is not None:
            self.meta_title = title
        if description is not None:
            self.meta_description = description
        if keywords is not None:
            self.meta_keywords = keywords

    def get_meta(self) -> dict[str, Optional[str]]:
        return {
            "meta_title": self.meta_title,
            "meta_description": self.meta_description,
            "meta_keywords": self.meta_keywords,
        }

    @property
    def current_price(self) -> Optional[Decimal]:
        return self.sale_price if self.sale_price is not None else self.price

    @property
    def margin(self) -> Optional[Decimal]:
        if self.current_price is None or self.cost_price is None:
            return None
        return self.current_price - self.cost_price

    @property
    def margin_percent(self) -> Optional[Decimal]:
        if self.current_price is None or self.cost_price is None or self.current_price == 0:
            return None
        return (self.current_price - self.cost_price) * Decimal("100") / self.current_price

    def _dispatch_audit(
        self, session: Optional[Session], action: str, details: dict[str, Any]
    ) -> None:
        if Product.emit_audit_async_hook:
            Product.emit_audit_async_hook(session, self, action, details)
        else:
            try:
                from app.models.audit import AuditLog  # noqa: F401
            except Exception:
                from app.models.audit_log import AuditLog  # type: ignore
            self.audit_logs.append(
                AuditLog(
                    product_id=self.id,
                    action=action,
                    details=details or {},
                    created_at=datetime.utcnow(),
                )
            )

    def emit_audit(
        self, action: str, details: dict[str, Any], actor_id: Optional[int] = None
    ) -> None:
        payload = dict(details or {})
        if actor_id is not None:
            payload["actor_id"] = actor_id
        self._dispatch_audit(None, action, payload)

    # Stock (агрегаты)
    def _apply_stock(
        self,
        delta: int,
        movement_type: str,
        user_id: Optional[int] = None,
        note: Optional[str] = None,
        *,
        actor_id: Optional[int] = None,
    ) -> None:
        new_q = int(self.stock_quantity or 0) + delta
        if new_q < 0:
            raise ValueError("Resulting stock cannot be negative")
        old_q = self.stock_quantity
        self.stock_quantity = new_q
        self.emit_audit(
            "stock_movement",
            {
                "movement_type": movement_type,
                "delta": delta,
                "old_qty": old_q,
                "new_qty": new_q,
                "note": note,
                "user_id": user_id,
            },
            actor_id=actor_id,
        )

    def log_stock_movement(
        self,
        movement_type: str,
        qty: int,
        user_id: Optional[int] = None,
        note: Optional[str] = None,
        *,
        actor_id: Optional[int] = None,
    ) -> None:
        if movement_type not in ("in", "out", "adjustment", "return"):
            raise ValueError("Invalid movement_type")
        if qty <= 0:
            raise ValueError("qty must be positive")
        delta = (
            qty if movement_type in ("in", "return") else (-qty if movement_type == "out" else qty)
        )
        self._apply_stock(delta, movement_type, user_id, note, actor_id=actor_id)

    def receive(
        self,
        qty: int,
        user_id: Optional[int] = None,
        note: Optional[str] = None,
        *,
        actor_id: Optional[int] = None,
    ) -> None:
        if qty <= 0:
            raise ValueError("qty must be positive")
        self._apply_stock(qty, "in", user_id, note, actor_id=actor_id)

    def ship(
        self,
        qty: int,
        user_id: Optional[int] = None,
        note: Optional[str] = None,
        *,
        actor_id: Optional[int] = None,
    ) -> None:
        if qty <= 0:
            raise ValueError("qty must be positive")
        self._apply_stock(-qty, "out", user_id, note, actor_id=actor_id)

    def adjust(
        self,
        qty_delta: int,
        user_id: Optional[int] = None,
        note: Optional[str] = None,
        *,
        actor_id: Optional[int] = None,
    ) -> None:
        if qty_delta == 0:
            return
        self._apply_stock(qty_delta, "adjustment", user_id, note, actor_id=actor_id)

    def reserve(self, qty: int, *, actor_id: Optional[int] = None) -> None:
        if qty <= 0:
            raise ValueError("qty must be positive")
        if (self.stock_quantity - self.reserved_quantity) < qty:
            raise ValueError("insufficient free stock to reserve")
        self.reserved_quantity += qty
        self.emit_audit(
            "reserve_stock", {"qty": qty, "reserved": self.reserved_quantity}, actor_id=actor_id
        )

    def release(self, qty: int, *, actor_id: Optional[int] = None) -> None:
        if qty <= 0:
            raise ValueError("qty must be positive")
        if self.reserved_quantity < qty:
            raise ValueError("cannot release more than reserved")
        self.reserved_quantity -= qty
        self.emit_audit(
            "release_stock", {"qty": qty, "reserved": self.reserved_quantity}, actor_id=actor_id
        )

    @property
    def free_stock(self) -> int:
        return max(0, int(self.stock_quantity) - int(self.reserved_quantity))

    @property
    def is_low_stock(self) -> bool:
        return self.free_stock <= (self.min_stock_level or 0)

    @property
    def has_variants(self) -> bool:
        return len(self.variants) > 0

    # Gallery (JSON-text)
    def get_gallery_urls(self) -> list[str]:
        return _ensure_json_list_of_str(self.gallery_urls, allow_none=True)

    def set_gallery_urls(self, urls: list[str]) -> None:
        clean = _ensure_json_list_of_str(urls, allow_none=True)
        self.gallery_urls = json.dumps(clean)

    # Availability / preorder
    def can_fulfill(self, qty: int) -> bool:
        return qty >= 0 and self.free_stock >= qty

    def sync_stock_from_locations(self) -> int:
        total = sum(int(s.quantity or 0) for s in self.stocks)
        self.stock_quantity = total
        return total

    def sync_stock_from_variants(self) -> tuple[int, int]:
        total = sum(int(v.stock_quantity or 0) for v in self.variants if not v.deleted_at)
        self.stock_quantity = total
        self.reserved_quantity = min(self.reserved_quantity, self.stock_quantity)
        return total, self.reserved_quantity

    def get_stocks(self) -> list[ProductStock]:
        return self.stocks

    def get_audit_logs(self) -> list[AuditLog]:
        return self.audit_logs

    def get_stock_movements(self, limit: int = 10) -> list[StockMovement]:
        return self.stock_movements[-limit:]

    def is_available(self) -> bool:
        return self.is_active and not self.deleted_at and self.free_stock > 0

    def is_preorder(self) -> bool:
        now_ts = int(datetime.utcnow().timestamp())
        return bool(self.is_preorder_enabled) and (
            self.preorder_until is None or self.preorder_until > now_ts
        )

    def get_category_path(self) -> str:
        return self.category.get_full_path() if self.category else ""

    def activate(self) -> None:
        self.is_active = True

    def deactivate(self) -> None:
        self.is_active = False

    def set_featured(self, value: bool = True) -> None:
        self.is_featured = bool(value)

    # ---------- Preorder helpers ----------
    def enable_preorder(
        self,
        *,
        lead_days: Optional[int] = None,
        deposit: Optional[Decimal] = None,
        note: Optional[str] = None,
        show_zero_stock: Optional[bool] = None,
    ) -> None:
        """
        Включает предзаказ.
        - lead_days: на сколько дней вперёд выставлять срок доставки (preorder_until = now + days)
        - deposit: необязательный задаток (<= price)
        - note: произвольная пометка
        - show_zero_stock: если True — показываем «0 в наличии», если False — «без остатка»
        """
        self.is_preorder_enabled = True
        if lead_days is not None:
            if lead_days < 0:
                raise ValueError("lead_days cannot be negative")
            self.preorder_lead_days = lead_days
            self.preorder_until = int((datetime.utcnow() + timedelta(days=lead_days)).timestamp())
        if deposit is not None:
            if self.price is not None and deposit > self.price:
                raise ValueError("preorder_deposit cannot exceed price")
            self.preorder_deposit = deposit
        if note is not None:
            self.preorder_note = note
        if show_zero_stock is not None:
            self.preorder_show_zero_stock = bool(show_zero_stock)

    def disable_preorder(self) -> None:
        self.is_preorder_enabled = False
        self.preorder_until = None
        self.preorder_lead_days = None
        self.preorder_note = None
        self.preorder_deposit = None

    def auto_preorder_on_depletion(
        self, *, enable: bool = True, default_lead_days: int = 20
    ) -> None:
        """
        Автоматический предзаказ при обнулении остатков.
        Флаг хранится в extra["preorder"]["auto_on_depletion"] и extra["preorder"]["default_lead_days"].
        """
        data = self.get_extra()
        preorder_cfg = _ensure_json_dict(data.get("preorder", {}), allow_none=True)
        preorder_cfg["auto_on_depletion"] = bool(enable)
        preorder_cfg["default_lead_days"] = int(default_lead_days)
        data["preorder"] = preorder_cfg
        self.extra = json.dumps(data)

    # ---------- Price helpers ----------
    def clamp_price_to_bounds(self) -> None:
        """
        Приводит текущие price/sale_price к диапазону [min_price, max_price] при их наличии.
        """
        if self.price is not None:
            if self.min_price is not None and self.price < self.min_price:
                self.price = self.min_price
            if self.max_price is not None and self.price > self.max_price:
                self.price = self.max_price
        if self.sale_price is not None:
            # sale_price <= price и >= min_price (если заданы)
            if self.price is not None and self.sale_price > self.price:
                self.sale_price = self.price
            if self.min_price is not None and self.sale_price < self.min_price:
                self.sale_price = self.min_price

    def set_price_guarded(
        self,
        new_price: Decimal,
        *,
        as_sale: bool = False,
        update_timestamps: bool = True,
        respect_bounds: bool = True,
    ) -> None:
        """
        Устанавливает цену с учётом ограничений, инвариантов и служебных таймстемпов.
        """
        if new_price is None or new_price < 0:
            raise ValueError("new_price must be non-negative")
        if as_sale:
            self.sale_price = new_price
        else:
            self.price = new_price
        if respect_bounds:
            self.clamp_price_to_bounds()
        if update_timestamps:
            self.price_updated_at = datetime.utcnow()

    def sync_prices_from_variants(
        self, session: Optional[Session] = None, *, commit: bool = False
    ) -> tuple[Optional[Decimal], Optional[Decimal]]:
        active = [v for v in self.variants if not v.deleted_at and v.is_active]
        prices = [v.effective_price for v in active if v.effective_price is not None]
        if not prices:
            return self.min_price, self.max_price
        mn = min(prices)
        mx = max(prices)
        self.min_price = mn
        self.max_price = mx
        if self.price is None:
            self.price = (sum(prices, Decimal(0)) / Decimal(len(prices))).quantize(Decimal("0.01"))
        if session and commit:
            session.commit()
        return self.min_price, self.max_price

    # Валидация агрегатов
    def validate(self) -> None:
        if not self.name or not self.sku or not self.slug:
            raise ValueError("Product must have name, SKU, and slug")
        if self.price is None or self.price < 0:
            raise ValueError("Product price must be non-negative")
        if self.stock_quantity is None or self.stock_quantity < 0:
            raise ValueError("Stock quantity cannot be negative")
        if self.reserved_quantity is None or self.reserved_quantity < 0:
            raise ValueError("Reserved quantity cannot be negative")
        if self.reserved_quantity > self.stock_quantity:
            raise ValueError("Reserved quantity cannot exceed stock")
        if (
            self.min_price is not None
            and self.max_price is not None
            and self.min_price > self.max_price
        ):
            raise ValueError("min_price cannot be greater than max_price")
        if self.sale_price is not None and self.price is not None and self.sale_price > self.price:
            raise ValueError("sale_price cannot exceed price")
        if (
            self.preorder_deposit is not None
            and self.price is not None
            and self.preorder_deposit > self.price
        ):
            raise ValueError("preorder_deposit cannot exceed price")

    # Import/export
    @classmethod
    def import_from_dict(cls, session: Session, data: dict[str, Any]) -> Product:
        from app.models.warehouse import ProductStock  # локально, чтобы не создавать циклы

        gallery = data.get("gallery_urls")
        if isinstance(gallery, list):
            data["gallery_urls"] = json.dumps(_ensure_json_list_of_str(gallery))
        extra = data.get("extra")
        if isinstance(extra, dict):
            data["extra"] = json.dumps(_ensure_json_dict(extra))
        obj = cls(**{k: v for k, v in data.items() if k in cls.__table__.columns.keys()})
        session.add(obj)
        session.commit()
        for v in data.get("variants", []):
            variant = ProductVariant.import_from_dict(session, {**v, "product_id": obj.id})
            obj.variants.append(variant)
        for s in data.get("stocks", []):
            stock = ProductStock.import_from_dict(session, {**s, "product_id": obj.id})
            obj.stocks.append(stock)
        session.commit()
        return obj

    @classmethod
    def export_to_dict(cls, obj: Product, json_safe: bool = False) -> dict[str, Any]:
        return obj.to_dict(
            with_variants=True, with_stocks=True, with_audit=True, json_safe=json_safe
        )

    @classmethod
    def export_csv(cls, products: Iterable[Product]) -> str:
        headers = [
            "id",
            "company_id",
            "name",
            "slug",
            "sku",
            "price",
            "min_price",
            "max_price",
            "cost_price",
            "sale_price",
            "stock_quantity",
            "reserved_quantity",
            "min_stock_level",
            "max_stock_level",
            "is_active",
            "is_featured",
            "is_preorder_enabled",
            "preorder_until",
            "preorder_deposit",
            "preorder_note",
            "preorder_lead_days",
            "preorder_show_zero_stock",
            "enable_price_dumping",
            "exclude_friendly_stores",
            "repriced_at",
            "price_updated_at",
            "category_id",
            "image_url",
            "image_public_id",
            "meta_title",
            "meta_description",
            "meta_keywords",
            "kaspi_product_id",
            "kaspi_status",
            "deleted_at",
            "created_at",
            "updated_at",
            "gallery_urls",
            "extra",
        ]
        sio = io.StringIO()
        w = csv.DictWriter(sio, fieldnames=headers)
        w.writeheader()
        for p in products:
            row = _row_to_dict(p, json_safe=True)
            # нормализуем JSON-тексты
            row["gallery_urls"] = row.get("gallery_urls") or "[]"
            row["extra"] = row.get("extra") or "{}"
            row.setdefault("reserved_quantity", _jsonify_value(getattr(p, "reserved_quantity", 0)))
            row = {k: row.get(k, "") for k in headers}
            w.writerow(row)
        return sio.getvalue()

    # Slug uniqueness / Clone
    @staticmethod
    def _make_unique_slug(session: Session, base_slug: str, company_id: Optional[int]) -> str:
        base = (base_slug or "").strip().lower() or "product"
        candidates = [base, f"{base}-copy"]
        for i in range(2, 1000):
            candidates.append(f"{base}-copy-{i}")
        existing = {
            r[0]
            for r in session.execute(
                select(Product.slug).where(
                    Product.company_id == company_id, Product.slug.in_(candidates)
                )
            ).all()
        }
        for cand in candidates:
            if cand not in existing:
                return cand
        return f"{base}-{int(datetime.utcnow().timestamp())}"

    def clone_shallow(self, session: Optional[Session] = None) -> Product:
        copy_slug = self.slug or "product"
        if session is not None:
            copy_slug = self._make_unique_slug(session, copy_slug, self.company_id)
        else:
            copy_slug = f"{copy_slug}-copy"
        cp = Product(
            company_id=self.company_id,
            name=f"{self.name} (Copy)",
            slug=copy_slug,
            sku=None,
            description=self.description,
            short_description=self.short_description,
            price=self.price,
            min_price=self.min_price,
            max_price=self.max_price,
            cost_price=self.cost_price,
            sale_price=None,
            stock_quantity=0,
            reserved_quantity=0,
            min_stock_level=self.min_stock_level,
            max_stock_level=self.max_stock_level,
            is_active=False,
            is_featured=False,
            is_preorder_enabled=False,
            preorder_until=None,
            preorder_deposit=None,
            preorder_note=None,
            preorder_lead_days=self.preorder_lead_days,
            preorder_show_zero_stock=self.preorder_show_zero_stock,
            enable_price_dumping=self.enable_price_dumping,
            exclude_friendly_stores=self.exclude_friendly_stores,
            category_id=self.category_id,
            image_url=self.image_url,
            image_public_id=self.image_public_id,
            gallery_urls=self.gallery_urls,
            meta_title=self.meta_title,
            meta_description=self.meta_description,
            meta_keywords=self.meta_keywords,
            kaspi_product_id=None,
            kaspi_status=None,
            extra=self.extra,
        )
        return cp

    def clone_deep(self, session: Session) -> Product:
        cp = self.clone_shallow(session)
        session.add(cp)
        session.flush()
        for v in self.variants:
            nv = ProductVariant(
                product_id=cp.id,
                sku=f"{v.sku}-copy" if v.sku else None,
                name=v.name,
                price=v.price,
                cost_price=v.cost_price,
                sale_price=None,
                stock_quantity=0,
                attributes=v.attributes,
                is_active=False,
                image_url=v.image_url,
            )
            session.add(nv)
        session.commit()
        return cp

    # Sync prices from variants (already above but left for API compatibility)
    # (kept as-is — см. выше реализацию)
    # ----------------------------------------------------------


# =============================================================================
# ProductVariant
# =============================================================================
class ProductVariant(LenientInitMixin, VersionedMixin, BaseModel):
    __tablename__ = "product_variants"
    __allow_unmapped__ = True

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    product_id: Mapped[int] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"), nullable=False, index=True
    )

    sku: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    price: Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 2))
    cost_price: Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 2))
    sale_price: Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 2))

    stock_quantity: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    attributes: Mapped[Optional[str]] = mapped_column(Text)

    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=text("true"), index=True
    )
    image_url: Mapped[Optional[str]] = mapped_column(String(500))

    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime, index=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    product: Mapped[Product] = relationship("Product", back_populates="variants")

    __table_args__ = (
        UniqueConstraint("product_id", "sku", name="uq_variant_product_sku"),
        Index("ix_variant_product_active", "product_id", "is_active"),
        CheckConstraint("stock_quantity >= 0", name="ck_variant_stock_nonneg"),
        CheckConstraint("(price IS NULL OR price >= 0)", name="ck_variant_price_nonneg"),
        CheckConstraint(
            "(sale_price IS NULL OR sale_price >= 0)", name="ck_variant_sale_price_nonneg"
        ),
        CheckConstraint(
            "(sale_price IS NULL OR price IS NULL OR sale_price <= price)",
            name="ck_variant_sale_le_price",
        ),
        {"extend_existing": True},
    )

    @validates("sku", "name")
    def _norm_required(self, key: str, v: Optional[str]) -> str:
        vv = (v or "").strip()
        if not vv and key == "name":
            raise ValueError("name cannot be empty")
        return vv.lower() if (key == "sku" and vv) else vv

    @validates("price", "cost_price", "sale_price")
    def _validate_money(self, key: str, value: Optional[Decimal]) -> Optional[Decimal]:
        if value is not None and value < 0:
            raise ValueError(f"{key} cannot be negative")
        return value

    @validates("stock_quantity")
    def _validate_stock(self, _key: str, value: Optional[int]) -> Optional[int]:
        if value is not None and value < 0:
            raise ValueError("stock_quantity cannot be negative")
        return value

    def __repr__(self) -> str:
        return f"<ProductVariant(id={self.id}, product_id={self.product_id}, sku='{self.sku}', name='{self.name}')>"

    # Derived
    @property
    def display_label(self) -> str:
        sku = f"[{self.sku}]" if self.sku else ""
        return f"{self.name or ''} {sku}".strip()

    @property
    def is_archived(self) -> bool:
        return self.deleted_at is not None

    @property
    def search_text(self) -> str:
        parts = [self.name or "", self.sku or ""]
        return " ".join(parts).strip().lower()

    @property
    def full_search_text(self) -> str:
        p = getattr(self, "product", None)
        parts = [self.search_text]
        if p:
            parts.extend([p.name or "", p.sku or "", p.slug or ""])
            if getattr(p, "category", None):
                parts.append(p.category.get_full_path())
        return " ".join(s for s in parts if s).strip().lower()

    @hybrid_property
    def effective_price(self) -> Optional[Decimal]:
        return (
            self.sale_price
            or self.price
            or (self.product.sale_price if self.product else None)
            or (self.product.price if self.product else None)
        )

    # JSON attrs
    def set_attributes(self, attributes: dict[str, Any]) -> None:
        clean = _ensure_json_dict(attributes, allow_none=True)
        self.attributes = json.dumps(clean)

    def get_attributes(self) -> dict[str, Any]:
        if self.attributes:
            try:
                return json.loads(self.attributes)
            except Exception:
                return {}
        return {}

    # Stock helpers
    @property
    def is_low_stock(self) -> bool:
        return self.stock_quantity <= 0

    def add_stock(self, qty: int) -> None:
        if qty < 0:
            raise ValueError("Cannot add negative quantity")
        self.stock_quantity += qty

    def remove_stock(self, qty: int) -> None:
        if qty < 0:
            raise ValueError("Cannot remove negative quantity")
        self.stock_quantity = max(0, self.stock_quantity - qty)

    def get_product(self) -> Product:
        return self.product  # type: ignore[return-value]

    def validate(self) -> None:
        if not self.name:
            raise ValueError("Variant must have name")
        if self.price is not None and self.price < 0:
            raise ValueError("Variant price must be non-negative")
        if self.stock_quantity < 0:
            raise ValueError("Variant stock quantity cannot be negative")

    # Bulk upsert
    @classmethod
    def bulk_upsert(
        cls,
        session: Session,
        product_id: int,
        rows: Iterable[dict[str, Any]],
        by: str = "sku",
        *,
        atomic: bool = True,
        return_report: bool = False,
        actor_id: Optional[int] = None,
        audit_on_error: bool = True,
        audit_error_cap: int = 20,
        file_logging: bool = True,
    ):
        tx = session.begin() if atomic else None
        items: list[ProductVariant] = []
        errors: list[dict[str, Any]] = []
        logger = _get_bulk_logger() if file_logging else None

        try:
            existing = {
                (v.sku or "").lower(): v
                for v in session.execute(
                    select(ProductVariant).where(
                        ProductVariant.product_id == product_id,
                        or_(
                            ProductVariant.deleted_at.is_(None), ProductVariant.deleted_at == None
                        ),  # noqa: E711
                    )
                ).scalars()
            }
            for raw in rows:
                try:
                    payload = {k: v for k, v in raw.items() if k in cls.__table__.columns}
                    key_value = str(payload.get(by, "")).strip().lower()
                    if not key_value:
                        raise ValueError(f"Missing '{by}'")
                    if by == "sku":
                        payload["sku"] = key_value
                    if key_value in existing:
                        v = existing[key_value]
                        for k, val in payload.items():
                            if k == "id":
                                continue
                            setattr(v, k, val)
                        items.append(v)
                    else:
                        v = cls(product_id=product_id, **payload)
                        session.add(v)
                        items.append(v)
                except Exception as e:
                    err = {"row": raw, "error": str(e)}
                    errors.append(err)
                    if logger:
                        logger.error(
                            "bulk_upsert error | product_id=%s | error=%s | row=%s",
                            product_id,
                            e,
                            raw,
                        )

            session.flush()
            if atomic and tx:
                tx.commit()
        except Exception as e:
            if atomic and tx:
                tx.rollback()
            err = {"row": None, "error": f"transaction_failed: {e}"}
            errors.append(err)
            if logger:
                logger.error(
                    "bulk_upsert transaction_failed | product_id=%s | error=%s", product_id, e
                )

        if audit_on_error and errors:
            product = session.get(Product, product_id)
            if product:
                sample = errors[:audit_error_cap]
                product.emit_audit(
                    "variant_bulk_upsert_errors",
                    {"error_count": len(errors), "sample": sample, "by": by},
                    actor_id=actor_id,
                )
                session.flush()

        if return_report:
            return {"items": items, "errors": errors}
        return items

    # Bulk soft-delete / restore
    @classmethod
    def bulk_soft_delete(
        cls, session: Session, product_id: int, ids: Optional[list[int]] = None
    ) -> int:
        now = datetime.utcnow()
        q = session.query(cls).filter(cls.product_id == product_id, cls.deleted_at.is_(None))
        if ids:
            q = q.filter(cls.id.in_(ids))
        count = 0
        for v in q:
            v.deleted_at = now
            count += 1
        session.flush()
        return count

    @classmethod
    def bulk_restore(
        cls, session: Session, product_id: int, ids: Optional[list[int]] = None
    ) -> int:
        q = session.query(cls).filter(cls.product_id == product_id, cls.deleted_at.is_not(None))
        if ids:
            q = q.filter(cls.id.in_(ids))
        count = 0
        for v in q:
            v.deleted_at = None
            count += 1
        session.flush()
        return count

    # Import/export
    @classmethod
    def import_from_dict(cls, session: Session, data: dict[str, Any]) -> ProductVariant:
        attrs = data.get("attributes")
        if isinstance(attrs, dict):
            data["attributes"] = json.dumps(_ensure_json_dict(attrs))
        obj = cls(**{k: v for k, v in data.items() if k in cls.__table__.columns.keys()})
        session.add(obj)
        session.commit()
        return obj

    @classmethod
    def export_to_dict(cls, obj: ProductVariant, json_safe: bool = False) -> dict[str, Any]:
        data = obj.to_dict(json_safe=json_safe)
        return data

    # Query helpers
    @classmethod
    def get_by_sku(cls, session: Session, product_id: int, sku: str) -> Optional[ProductVariant]:
        return (
            session.query(cls)
            .filter(cls.product_id == product_id, cls.sku == (sku or "").strip().lower())
            .first()
        )

    @classmethod
    def search(
        cls,
        session: Session,
        product_id: int,
        q: Optional[str] = None,
        only_active: bool = True,
        limit: int = 100,
    ) -> list[ProductVariant]:
        query = session.query(cls).filter(cls.product_id == product_id)
        if only_active:
            query = query.filter(and_(cls.is_active.is_(True), cls.deleted_at.is_(None)))
        if q:
            like = f"%{(q or '').strip().lower()}%"
            query = query.filter(or_(cls.name.ilike(like), cls.sku.ilike(like)))
        return query.order_by(cls.updated_at.desc()).limit(limit).all()

    @classmethod
    def get_or_create(
        cls,
        session: Session,
        *,
        product_id: int,
        sku: str,
        defaults: Optional[dict[str, Any]] = None,
    ) -> tuple[ProductVariant, bool]:
        sku_norm = (sku or "").strip().lower()
        obj = cls.get_by_sku(session, product_id, sku_norm)
        if obj:
            return obj, False
        payload = dict(defaults or {})
        payload["product_id"] = product_id
        payload["sku"] = sku_norm
        obj = cls(**{k: v for k, v in payload.items() if k in cls.__table__.columns.keys()})
        session.add(obj)
        session.commit()
        return obj, True

    def move_to_product(self, session: Session, new_product_id: int) -> None:
        existing = ProductVariant.get_by_sku(session, new_product_id, self.sku or "")
        if existing and existing.id != self.id:
            raise ValueError("SKU already exists in target product")
        self.product_id = new_product_id
        session.flush()

    def to_dict(self, json_safe: bool = False) -> dict[str, Any]:
        data = _row_to_dict(self, json_safe=json_safe)
        if data.get("attributes"):
            try:
                data["attributes"] = json.loads(data["attributes"])
            except Exception:
                data["attributes"] = {}
        data["display_label"] = self.display_label
        data["is_archived"] = self.is_archived
        data["search_text"] = self.search_text
        data["full_search_text"] = self.full_search_text
        return data


__all__ = ["Category", "Product", "ProductVariant"]
