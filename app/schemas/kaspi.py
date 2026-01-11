from typing import Any

from pydantic import BaseModel, Field


class KaspiTokenIn(BaseModel):
    store_name: str = Field(min_length=2, max_length=120)
    token: str = Field(min_length=10)


class KaspiTokenOut(BaseModel):
    store_name: str


class KaspiConnectIn(BaseModel):
    """Schema for Kaspi store connection (onboarding).

    This is the main entry point for connecting a Kaspi store to a tenant company.
    """

    company_name: str = Field(..., min_length=2, max_length=255, description="Company name to save in tenant")
    store_name: str = Field(..., min_length=3, max_length=120, description="Store name in Kaspi marketplace")
    token: str = Field(..., min_length=10, description="Kaspi API token")
    verify: bool = Field(True, description="Verify token with Kaspi adapter before saving")
    meta: dict[str, Any] | None = Field(None, description="Optional private marketplace metadata (not exposed)")

    @staticmethod
    def validate_company_name(v: str) -> str:
        return v.strip()


class KaspiConnectOut(BaseModel):
    """Response from Kaspi store connection."""

    store_name: str
    company_id: int
    connected: bool = True
    message: str | None = None


class OrdersQuery(BaseModel):
    store: str
    state: str | None = None  # например: APPROVED_BY_BANK


class ImportRequest(BaseModel):
    store: str
    offers_json_path: str  # путь к .json, из которого Kaspi.ps1 соберёт XML-фид


class ImportStatusQuery(BaseModel):
    store: str
    import_id: str | None = None
