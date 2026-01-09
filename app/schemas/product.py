"""
Product-related Pydantic schemas with validation.
"""

from decimal import Decimal
from typing import Any

from pydantic import Field, ValidationInfo, field_validator

from app.schemas.base import BaseSchema, TimestampedSchema


class CategoryBase(BaseSchema):
    """Base category schema."""

    name: str = Field(..., min_length=1, max_length=255, description="Category name")
    slug: str = Field(..., min_length=1, max_length=255, description="URL-friendly slug")
    description: str | None = Field(None, description="Category description")
    parent_id: int | None = Field(None, description="Parent category ID")
    is_active: bool = Field(default=True, description="Whether category is active")
    sort_order: int = Field(default=0, ge=0, description="Sort order")

    @field_validator("slug", mode="before")
    def validate_slug(cls, v):
        """Validate slug format."""
        import re

        if not re.match(r"^[a-z0-9-]+$", v):
            raise ValueError("Slug must contain only lowercase letters, numbers, and hyphens")
        return v


class CategoryCreate(CategoryBase):
    """Schema for category creation."""

    pass


class CategoryUpdate(BaseSchema):
    """Schema for category update."""

    name: str | None = Field(None, min_length=1, max_length=255)
    slug: str | None = Field(None, min_length=1, max_length=255)
    description: str | None = None
    parent_id: int | None = None
    is_active: bool | None = None
    sort_order: int | None = Field(None, ge=0)

    @field_validator("slug", mode="before")
    def validate_slug(cls, v):
        """Validate slug format."""
        if v is not None:
            import re

            if not re.match(r"^[a-z0-9-]+$", v):
                raise ValueError("Slug must contain only lowercase letters, numbers, and hyphens")
        return v


class CategoryResponse(CategoryBase, TimestampedSchema):
    """Schema for category response."""

    pass


class ProductBase(BaseSchema):
    """Base product schema."""

    name: str = Field(..., min_length=1, max_length=255, description="Product name")
    slug: str = Field(..., min_length=1, max_length=255, description="URL-friendly slug")
    sku: str = Field(..., min_length=1, max_length=100, description="Stock Keeping Unit")
    description: str | None = Field(None, description="Product description")
    short_description: str | None = Field(None, max_length=500, description="Short description")

    # Pricing
    price: Decimal = Field(..., gt=0, max_digits=10, decimal_places=2, description="Product price")
    cost_price: Decimal | None = Field(None, ge=0, max_digits=10, decimal_places=2, description="Cost price")
    sale_price: Decimal | None = Field(None, ge=0, max_digits=10, decimal_places=2, description="Sale price")

    # Inventory
    stock_quantity: int = Field(default=0, ge=0, description="Stock quantity")
    min_stock_level: int = Field(default=0, ge=0, description="Minimum stock level")
    max_stock_level: int | None = Field(None, ge=0, description="Maximum stock level")

    # Status
    is_active: bool = Field(default=True, description="Whether product is active")
    is_featured: bool = Field(default=False, description="Whether product is featured")
    is_digital: bool = Field(default=False, description="Whether product is digital")

    # SEO
    meta_title: str | None = Field(None, max_length=255, description="SEO title")
    meta_description: str | None = Field(None, max_length=500, description="SEO description")
    meta_keywords: str | None = Field(None, max_length=500, description="SEO keywords")

    # Category
    category_id: int | None = Field(None, description="Category ID")

    # Media
    image_url: str | None = Field(None, max_length=500, description="Main image URL")
    gallery_urls: list[str] | None = Field(None, description="Gallery image URLs")

    # Dimensions
    weight: Decimal | None = Field(None, ge=0, max_digits=8, decimal_places=3, description="Weight in kg")
    length: Decimal | None = Field(None, ge=0, max_digits=8, decimal_places=2, description="Length in cm")
    width: Decimal | None = Field(None, ge=0, max_digits=8, decimal_places=2, description="Width in cm")
    height: Decimal | None = Field(None, ge=0, max_digits=8, decimal_places=2, description="Height in cm")

    @field_validator("slug", mode="before")
    def validate_slug(cls, v):
        """Validate slug format."""
        import re

        if not re.match(r"^[a-z0-9-]+$", v):
            raise ValueError("Slug must contain only lowercase letters, numbers, and hyphens")
        return v

    @field_validator("sku", mode="before")
    def validate_sku(cls, v):
        """Validate SKU format."""
        import re

        if not re.match(r"^[A-Z0-9-_]+$", v):
            raise ValueError("SKU must contain only uppercase letters, numbers, hyphens, and underscores")
        return v

    @field_validator("sale_price", mode="after")
    def validate_sale_price(cls, v, info: ValidationInfo):
        """Validate sale price is less than regular price."""
        price = (info.data or {}).get("price")
        if v is not None and price is not None and v >= price:
            raise ValueError("Sale price must be less than regular price")
        return v

    @field_validator("max_stock_level", mode="after")
    def validate_max_stock_level(cls, v, info: ValidationInfo):
        """Validate max stock level is greater than min stock level."""
        min_stock = (info.data or {}).get("min_stock_level")
        if v is not None and min_stock is not None and v <= min_stock:
            raise ValueError("Max stock level must be greater than min stock level")
        return v

    @field_validator("gallery_urls", mode="after")
    def validate_gallery_urls(cls, v):
        """Validate gallery URLs."""
        if v is not None:
            for url in v:
                if not url.startswith(("http://", "https://")):
                    raise ValueError("Gallery URLs must be valid HTTP/HTTPS URLs")
        return v


class ProductCreate(ProductBase):
    """Schema for product creation."""

    pass


class ProductUpdate(BaseSchema):
    """Schema for product update."""

    name: str | None = Field(None, min_length=1, max_length=255)
    slug: str | None = Field(None, min_length=1, max_length=255)
    description: str | None = None
    short_description: str | None = Field(None, max_length=500)

    price: Decimal | None = Field(None, gt=0, max_digits=10, decimal_places=2)
    cost_price: Decimal | None = Field(None, ge=0, max_digits=10, decimal_places=2)
    sale_price: Decimal | None = Field(None, ge=0, max_digits=10, decimal_places=2)

    stock_quantity: int | None = Field(None, ge=0)
    min_stock_level: int | None = Field(None, ge=0)
    max_stock_level: int | None = Field(None, ge=0)

    is_active: bool | None = None
    is_featured: bool | None = None
    is_digital: bool | None = None

    meta_title: str | None = Field(None, max_length=255)
    meta_description: str | None = Field(None, max_length=500)
    meta_keywords: str | None = Field(None, max_length=500)

    category_id: int | None = None
    image_url: str | None = Field(None, max_length=500)
    gallery_urls: list[str] | None = None

    weight: Decimal | None = Field(None, ge=0, max_digits=8, decimal_places=3)
    length: Decimal | None = Field(None, ge=0, max_digits=8, decimal_places=2)
    width: Decimal | None = Field(None, ge=0, max_digits=8, decimal_places=2)
    height: Decimal | None = Field(None, ge=0, max_digits=8, decimal_places=2)

    @field_validator("slug", mode="before")
    def validate_slug(cls, v):
        """Validate slug format."""
        if v is not None:
            import re

            if not re.match(r"^[a-z0-9-]+$", v):
                raise ValueError("Slug must contain only lowercase letters, numbers, and hyphens")
        return v

    @field_validator("sale_price", mode="after")
    def validate_sale_price(cls, v, info: ValidationInfo):
        """Validate sale price is less than regular price."""
        price = (info.data or {}).get("price")
        if v is not None and price is not None and v >= price:
            raise ValueError("Sale price must be less than regular price")
        return v


class ProductResponse(ProductBase, TimestampedSchema):
    """Schema for product response."""

    category: CategoryResponse | None = None


class ProductVariantBase(BaseSchema):
    """Base product variant schema."""

    product_id: int = Field(..., gt=0, description="Product ID")
    sku: str = Field(..., min_length=1, max_length=100, description="Variant SKU")
    name: str = Field(..., min_length=1, max_length=255, description="Variant name")

    price: Decimal | None = Field(None, gt=0, max_digits=10, decimal_places=2, description="Variant price")
    cost_price: Decimal | None = Field(None, ge=0, max_digits=10, decimal_places=2, description="Cost price")
    sale_price: Decimal | None = Field(None, ge=0, max_digits=10, decimal_places=2, description="Sale price")

    stock_quantity: int = Field(default=0, ge=0, description="Stock quantity")

    attributes: dict[str, Any] | None = Field(None, description="Variant attributes")
    is_active: bool = Field(default=True, description="Whether variant is active")
    image_url: str | None = Field(None, max_length=500, description="Variant image URL")

    @field_validator("sku", mode="before")
    def validate_sku(cls, v):
        """Validate SKU format."""
        import re

        if not re.match(r"^[A-Z0-9-_]+$", v):
            raise ValueError("SKU must contain only uppercase letters, numbers, hyphens, and underscores")
        return v

    @field_validator("sale_price", mode="after")
    def validate_sale_price(cls, v, info: ValidationInfo):
        """Validate sale price is less than regular price."""
        price = (info.data or {}).get("price")
        if v is not None and price is not None and v >= price:
            raise ValueError("Sale price must be less than regular price")
        return v


class ProductVariantCreate(ProductVariantBase):
    """Schema for product variant creation."""

    pass


class ProductVariantUpdate(BaseSchema):
    """Schema for product variant update."""

    name: str | None = Field(None, min_length=1, max_length=255)
    price: Decimal | None = Field(None, gt=0, max_digits=10, decimal_places=2)
    cost_price: Decimal | None = Field(None, ge=0, max_digits=10, decimal_places=2)
    sale_price: Decimal | None = Field(None, ge=0, max_digits=10, decimal_places=2)
    stock_quantity: int | None = Field(None, ge=0)
    attributes: dict[str, Any] | None = None
    is_active: bool | None = None
    image_url: str | None = Field(None, max_length=500)


class ProductVariantResponse(ProductVariantBase, TimestampedSchema):
    """Schema for product variant response."""

    pass


class ProductSearchFilters(BaseSchema):
    """Schema for product search filters."""

    category_id: int | None = None
    min_price: Decimal | None = Field(None, ge=0)
    max_price: Decimal | None = Field(None, ge=0)
    is_active: bool | None = None
    is_featured: bool | None = None
    is_digital: bool | None = None
    in_stock: bool | None = None
    search: str | None = Field(None, min_length=1, max_length=255)

    @field_validator("max_price", mode="after")
    def validate_price_range(cls, v, info: ValidationInfo):
        """Validate price range."""
        min_price = (info.data or {}).get("min_price")
        if v is not None and min_price is not None and v <= min_price:
            raise ValueError("Max price must be greater than min price")
        return v
