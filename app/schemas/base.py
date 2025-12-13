"""
Base Pydantic schemas with common patterns (Pydantic v2-ready).
Не удаляет существующую структуру, только дополняет и модернизирует.
Сохраняет имена классов, чтобы не ломать текущие импорты и типы.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field, field_validator


class BaseSchema(BaseModel):
    """Base schema with common configuration (обновлено под Pydantic v2)."""

    model_config = ConfigDict(
        from_attributes=True,  # заменяет orm_mode
        validate_assignment=True,  # как просили в исходнике
        use_enum_values=True,  # сериализация enum через .value
        populate_by_name=True,  # разрешить приём как по имени поля, так и по alias
        extra="ignore",  # игнорировать лишние поля во входных данных
        str_strip_whitespace=True,  # авто-обрезка пробелов для строк
    )


class TimestampedSchema(BaseSchema):
    """Schema with timestamp fields."""

    id: int
    created_at: datetime
    updated_at: datetime


T = TypeVar("T")


class PaginatedResponse(BaseSchema, Generic[T]):
    """Generic paginated response schema."""

    items: list[T] = Field(..., description="List of items")
    total: int = Field(..., ge=0, description="Total number of items")
    page: int = Field(..., ge=1, description="Current page number")
    per_page: int = Field(..., ge=1, le=1000, description="Items per page")
    pages: int = Field(..., ge=0, description="Total number of pages")
    has_next: bool = Field(..., description="Whether there is a next page")
    has_prev: bool = Field(..., description="Whether there is a previous page")

    @classmethod
    def create(cls, items: list[T], total: int, page: int, per_page: int) -> PaginatedResponse[T]:
        """Create paginated response (сохранён исходный контракт)."""
        pages = (total + per_page - 1) // per_page if per_page > 0 else 0
        return cls(
            items=items,
            total=total,
            page=page,
            per_page=per_page,
            pages=pages,
            has_next=page < pages,
            has_prev=page > 1,
        )


class PageParams(BaseSchema):
    """Удобная схема приёма query-параметров для пагинации в роутерах."""

    page: int = Field(1, ge=1, description="Номер страницы, начиная с 1")
    per_page: int = Field(50, ge=1, le=1000, description="Размер страницы")

    @field_validator("per_page")
    @classmethod
    def _clamp_per_page(cls, v: int) -> int:
        if v < 1:
            return 1
        if v > 1000:
            return 1000
        return v


def paginate_list(items: list[T], page: int, per_page: int) -> PaginatedResponse[T]:
    """Пагинация in-memory списков (для моков/тестов)."""
    total = len(items)
    if per_page <= 0:
        per_page = 1
    start = (page - 1) * per_page
    end = start + per_page
    slice_ = items[start:end]
    return PaginatedResponse.create(slice_, total, page, per_page)


class ErrorResponse(BaseSchema):
    """Error response schema."""

    error: str = Field(..., description="Error message (machine-readable)")
    detail: str | None = Field(None, description="Detailed error information (human-readable)")
    code: str | None = Field(None, description="Error code")
    trace_id: str | None = Field(None, description="Correlation/Trace ID")


class SuccessResponse(BaseSchema):
    """Success response schema."""

    message: str = Field(..., description="Success message")
    data: dict[str, Any] | None = Field(None, description="Additional data")


class ResponseEnvelope(BaseSchema, Generic[T]):
    """
    Универсальная «обёртка» ответа:
    - status: "ok" | "error"
    - data: полезная нагрузка (при успехе)
    - error: объект ErrorResponse (при ошибке)
    """

    status: str = Field(..., description="ok|error")
    data: T | None = Field(None, description="Payload")
    error: ErrorResponse | None = Field(None, description="Error details")

    @classmethod
    def ok(cls, data: T | None = None) -> ResponseEnvelope[T]:
        return cls(status="ok", data=data)

    @classmethod
    def err(cls, error: ErrorResponse) -> ResponseEnvelope[Any]:
        return cls(status="error", error=error)


# Легаси-алиасы (совместимость)
BaseCreateSchema = BaseSchema
BaseUpdateSchema = BaseSchema
BaseResponseSchema = BaseSchema


__all__ = [
    "BaseSchema",
    "TimestampedSchema",
    "T",
    "PaginatedResponse",
    "PageParams",
    "paginate_list",
    "ErrorResponse",
    "SuccessResponse",
    "ResponseEnvelope",
    "BaseCreateSchema",
    "BaseUpdateSchema",
    "BaseResponseSchema",
]
