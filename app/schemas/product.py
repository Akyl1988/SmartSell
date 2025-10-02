"""
Product-related Pydantic schemas with validation.
"""

from decimal import Decimal
from typing import Any, Optional

from pydantic import Field, validator

from app.schemas.base import BaseSchema, TimestampedSchema


class CategoryBase(BaseSchema):
    """Base category schema."""

    name: str = Field(..., min_length=1, max_length=255, description="Category name")
    slug: str = Field(..., min_length=1, max_length=255, description="URL-friendly slug")
    description: Optional[str] = Field(None, description="Category description")
    parent_id: Optional[int] = Field(None, description="Parent category ID")
    is_active: bool = Field(default=True, description="Whether category is active")
    sort_order: int = Field(default=0, ge=0, description="Sort order")

    @validator("slug")
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

    name: Optional[str] = Field(None, min_length=1, max_length=255)
    slug: Optional[str] = Field(None, min_length=1, max_length=255)
    description: Optional[str] = None
    parent_id: Optional[int] = None
    is_active: Optional[bool] = None
    sort_order: Optional[int] = Field(None, ge=0)

    @validator("slug")
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
    description: Optional[str] = Field(None, description="Product description")
    short_description: Optional[str] = Field(None, max_length=500, description="Short description")

    # Pricing
    price: Decimal = Field(..., gt=0, max_digits=10, decimal_places=2, description="Product price")
    cost_price: Optional[Decimal] = Field(
        None, ge=0, max_digits=10, decimal_places=2, description="Cost price"
    )
    sale_price: Optional[Decimal] = Field(
        None, ge=0, max_digits=10, decimal_places=2, description="Sale price"
    )

    # Inventory
    stock_quantity: int = Field(default=0, ge=0, description="Stock quantity")
    min_stock_level: int = Field(default=0, ge=0, description="Minimum stock level")
    max_stock_level: Optional[int] = Field(None, ge=0, description="Maximum stock level")

    # Status
    is_active: bool = Field(default=True, description="Whether product is active")
    is_featured: bool = Field(default=False, description="Whether product is featured")
    is_digital: bool = Field(default=False, description="Whether product is digital")

    # SEO
    meta_title: Optional[str] = Field(None, max_length=255, description="SEO title")
    meta_description: Optional[str] = Field(None, max_length=500, description="SEO description")
    meta_keywords: Optional[str] = Field(None, max_length=500, description="SEO keywords")

    # Category
    category_id: Optional[int] = Field(None, description="Category ID")

    # Media
    image_url: Optional[str] = Field(None, max_length=500, description="Main image URL")
    gallery_urls: Optional[list[str]] = Field(None, description="Gallery image URLs")

    # Dimensions
    weight: Optional[Decimal] = Field(
        None, ge=0, max_digits=8, decimal_places=3, description="Weight in kg"
    )
    length: Optional[Decimal] = Field(
        None, ge=0, max_digits=8, decimal_places=2, description="Length in cm"
    )
    width: Optional[Decimal] = Field(
        None, ge=0, max_digits=8, decimal_places=2, description="Width in cm"
    )
    height: Optional[Decimal] = Field(
        None, ge=0, max_digits=8, decimal_places=2, description="Height in cm"
    )

    @validator("slug")
    def validate_slug(cls, v):
        """Validate slug format."""
        import re

        if not re.match(r"^[a-z0-9-]+$", v):
            raise ValueError("Slug must contain only lowercase letters, numbers, and hyphens")
        return v

    @validator("sku")
    def validate_sku(cls, v):
        """Validate SKU format."""
        import re

        if not re.match(r"^[A-Z0-9-_]+$", v):
            raise ValueError(
                "SKU must contain only uppercase letters, numbers, hyphens, and underscores"
            )
        return v

    @validator("sale_price")
    def validate_sale_price(cls, v, values):
        """Validate sale price is less than regular price."""
        if v is not None and "price" in values and v >= values["price"]:
            raise ValueError("Sale price must be less than regular price")
        return v

    @validator("max_stock_level")
    def validate_max_stock_level(cls, v, values):
        """Validate max stock level is greater than min stock level."""
        if v is not None and "min_stock_level" in values and v <= values["min_stock_level"]:
            raise ValueError("Max stock level must be greater than min stock level")
        return v

    @validator("gallery_urls")
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

    name: Optional[str] = Field(None, min_length=1, max_length=255)
    slug: Optional[str] = Field(None, min_length=1, max_length=255)
    description: Optional[str] = None
    short_description: Optional[str] = Field(None, max_length=500)

    price: Optional[Decimal] = Field(None, gt=0, max_digits=10, decimal_places=2)
    cost_price: Optional[Decimal] = Field(None, ge=0, max_digits=10, decimal_places=2)
    sale_price: Optional[Decimal] = Field(None, ge=0, max_digits=10, decimal_places=2)

    stock_quantity: Optional[int] = Field(None, ge=0)
    min_stock_level: Optional[int] = Field(None, ge=0)
    max_stock_level: Optional[int] = Field(None, ge=0)

    is_active: Optional[bool] = None
    is_featured: Optional[bool] = None
    is_digital: Optional[bool] = None

    meta_title: Optional[str] = Field(None, max_length=255)
    meta_description: Optional[str] = Field(None, max_length=500)
    meta_keywords: Optional[str] = Field(None, max_length=500)

    category_id: Optional[int] = None
    image_url: Optional[str] = Field(None, max_length=500)
    gallery_urls: Optional[list[str]] = None

    weight: Optional[Decimal] = Field(None, ge=0, max_digits=8, decimal_places=3)
    length: Optional[Decimal] = Field(None, ge=0, max_digits=8, decimal_places=2)
    width: Optional[Decimal] = Field(None, ge=0, max_digits=8, decimal_places=2)
    height: Optional[Decimal] = Field(None, ge=0, max_digits=8, decimal_places=2)

    @validator("slug")
    def validate_slug(cls, v):
        """Validate slug format."""
        if v is not None:
            import re

            if not re.match(r"^[a-z0-9-]+$", v):
                raise ValueError("Slug must contain only lowercase letters, numbers, and hyphens")
        return v

    @validator("sale_price")
    def validate_sale_price(cls, v, values):
        """Validate sale price is less than regular price."""
        if (
            v is not None
            and "price" in values
            and values["price"] is not None
            and v >= values["price"]
        ):
            raise ValueError("Sale price must be less than regular price")
        return v


class ProductResponse(ProductBase, TimestampedSchema):
    """Schema for product response."""

    category: Optional[CategoryResponse] = None


class ProductVariantBase(BaseSchema):
    """Base product variant schema."""

    product_id: int = Field(..., gt=0, description="Product ID")
    sku: str = Field(..., min_length=1, max_length=100, description="Variant SKU")
    name: str = Field(..., min_length=1, max_length=255, description="Variant name")

    price: Optional[Decimal] = Field(
        None, gt=0, max_digits=10, decimal_places=2, description="Variant price"
    )
    cost_price: Optional[Decimal] = Field(
        None, ge=0, max_digits=10, decimal_places=2, description="Cost price"
    )
    sale_price: Optional[Decimal] = Field(
        None, ge=0, max_digits=10, decimal_places=2, description="Sale price"
    )

    stock_quantity: int = Field(default=0, ge=0, description="Stock quantity")

    attributes: Optional[dict[str, Any]] = Field(None, description="Variant attributes")
    is_active: bool = Field(default=True, description="Whether variant is active")
    image_url: Optional[str] = Field(None, max_length=500, description="Variant image URL")

    @validator("sku")
    def validate_sku(cls, v):
        """Validate SKU format."""
        import re

        if not re.match(r"^[A-Z0-9-_]+$", v):
            raise ValueError(
                "SKU must contain only uppercase letters, numbers, hyphens, and underscores"
            )
        return v

    @validator("sale_price")
    def validate_sale_price(cls, v, values):
        """Validate sale price is less than regular price."""
        if v is not None and "price" in values and v >= values["price"]:
            raise ValueError("Sale price must be less than regular price")
        return v


class ProductVariantCreate(ProductVariantBase):
    """Schema for product variant creation."""

    pass


class ProductVariantUpdate(BaseSchema):
    """Schema for product variant update."""

    name: Optional[str] = Field(None, min_length=1, max_length=255)
    price: Optional[Decimal] = Field(None, gt=0, max_digits=10, decimal_places=2)
    cost_price: Optional[Decimal] = Field(None, ge=0, max_digits=10, decimal_places=2)
    sale_price: Optional[Decimal] = Field(None, ge=0, max_digits=10, decimal_places=2)
    stock_quantity: Optional[int] = Field(None, ge=0)
    attributes: Optional[dict[str, Any]] = None
    is_active: Optional[bool] = None
    image_url: Optional[str] = Field(None, max_length=500)


class ProductVariantResponse(ProductVariantBase, TimestampedSchema):
    """Schema for product variant response."""

    pass


class ProductSearchFilters(BaseSchema):
    """Schema for product search filters."""

    category_id: Optional[int] = None
    min_price: Optional[Decimal] = Field(None, ge=0)
    max_price: Optional[Decimal] = Field(None, ge=0)
    is_active: Optional[bool] = None
    is_featured: Optional[bool] = None
    is_digital: Optional[bool] = None
    in_stock: Optional[bool] = None
    search: Optional[str] = Field(None, min_length=1, max_length=255)

    @validator("max_price")
    def validate_price_range(cls, v, values):
        """Validate price range."""
        if (
            v is not None
            and "min_price" in values
            and values["min_price"] is not None
            and v <= values["min_price"]
        ):
            raise ValueError("Max price must be greater than min price")
        return v
