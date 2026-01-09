from pydantic import BaseModel, Field


class KaspiTokenIn(BaseModel):
    store_name: str = Field(min_length=2, max_length=120)
    token: str = Field(min_length=10)


class KaspiTokenOut(BaseModel):
    store_name: str


class OrdersQuery(BaseModel):
    store: str
    state: str | None = None  # например: APPROVED_BY_BANK


class ImportRequest(BaseModel):
    store: str
    offers_json_path: str  # путь к .json, из которого Kaspi.ps1 соберёт XML-фид


class ImportStatusQuery(BaseModel):
    store: str
    import_id: str | None = None
