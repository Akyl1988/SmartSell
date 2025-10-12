# app/schemas/wallet.py
from __future__ import annotations

"""
Pydantic-схемы для модуля Wallet.
- Совместимы с Pydantic v2.
- Decimal сериализуется в строку (json).
- Нормализация валюты до UPPER CASE, базовая валидация ISO-кода.
- Квантизация сумм до 6 знаков (DECIMAL(18,6)-friendly).
- Набор схем: аккаунт (create/out), операции (deposit/withdraw/transfer/out),
  баланс, ледгер (item/page), пагинация, обёртки страниц.
- Максимально стабильные контракты на уровне API.
"""

from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Generic, Literal, Optional, TypeVar

from pydantic import BaseModel, ConfigDict, Field, field_validator

# ====== Вспомогательные утилиты ==============================================

_DEC_PLACES = 6  # под DECIMAL(18,6)


def _to_decimal(value: Any) -> Decimal:
    """
    Приводит вход к Decimal через str(value), кидает ValueError при неуспехе.
    """
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        raise ValueError("Invalid decimal value")


def _quantize(d: Decimal, places: int = _DEC_PLACES) -> Decimal:
    """
    Квантизация Decimal (по умолчанию до 6 знаков, ROUND_HALF_UP).
    """
    q = Decimal(1) / (Decimal(10) ** places)
    return d.quantize(q, rounding=ROUND_HALF_UP)


def _normalize_currency(code: str) -> str:
    """
    Нормализация валютного кода к верхнему регистру и базовая валидация длины.
    Допускаем 3..10 символов, A–Z/цифры/подчёркивания/дефисы (на будущее).
    """
    if code is None:
        raise ValueError("currency is required")
    v = str(code).strip().upper()
    # Базовая проверка (строгий ISO-3 обычно: ^[A-Z]{3}$, но оставим гибкость 3..10)
    if not (3 <= len(v) <= 10):
        raise ValueError("currency must be 3..10 chars")
    return v


# ====== Общие базовые модели/конфиг ==========================================

_JSON_DECIMAL = {Decimal: lambda v: str(v)}

BaseJsonModel = BaseModel
BaseJsonModel.model_config = ConfigDict(
    from_attributes=True,
    populate_by_name=True,
    json_encoders=_JSON_DECIMAL,
)


# ====== Пагинация и обёртки ===================================================


class PaginationMeta(BaseModel):
    page: int = Field(1, ge=1)
    size: int = Field(20, ge=1, le=200)
    total: int = Field(0, ge=0)


T = TypeVar("T")


class Page(BaseModel, Generic[T]):
    items: list[T] = Field(default_factory=list)
    meta: PaginationMeta = Field(default_factory=PaginationMeta)


# ====== Аккаунты ==============================================================


class WalletAccountBase(BaseModel):
    """Базовая схема для кошелька."""

    user_id: int = Field(..., ge=1, description="ID пользователя-владельца")
    currency: str = Field(
        ..., min_length=3, max_length=10, description="Код валюты ISO-подобный (например, KZT, USD)"
    )
    model_config = ConfigDict(
        from_attributes=True,
        populate_by_name=True,
        json_encoders=_JSON_DECIMAL,
    )

    # Нормализуем валюту на входе
    @field_validator("currency", mode="before")
    @classmethod
    def _currency_norm(cls, v: Any) -> str:
        return _normalize_currency(v)


class WalletAccountCreate(WalletAccountBase):
    """Создание кошелька (поддержка начального баланса)."""

    balance: Optional[Decimal] = Field(default=Decimal("0"), description="Начальный баланс")

    @field_validator("balance", mode="before")
    @classmethod
    def _balance_to_decimal(cls, v: Any) -> Decimal:
        if v is None:
            return Decimal("0")
        return _quantize(_to_decimal(v))


class WalletAccountOut(WalletAccountBase):
    """Выдача кошелька наружу."""

    id: int
    balance: Decimal = Field(..., description="Текущий баланс")
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    @field_validator("balance", mode="before")
    @classmethod
    def _balance_to_decimal_out(cls, v: Any) -> Decimal:
        return _quantize(_to_decimal(v))


class WalletAccountsPage(Page[WalletAccountOut]):
    """Страница аккаунтов (items + meta)."""

    pass


# ====== Транзакции/операции ===================================================

TxType = Literal["deposit", "withdraw", "transfer_in", "transfer_out", "adjustment"]


class WalletTransactionBase(BaseModel):
    """Базовая схема транзакции (только сумма)."""

    amount: Decimal = Field(..., gt=0, description="Сумма транзакции")
    model_config = ConfigDict(json_encoders=_JSON_DECIMAL)

    @field_validator("amount", mode="before")
    @classmethod
    def _amount_to_decimal(cls, v: Any) -> Decimal:
        d = _to_decimal(v)
        if d <= 0:
            raise ValueError("amount must be > 0")
        return _quantize(d)


class WalletDeposit(WalletTransactionBase):
    """Запрос на депозит."""

    reference: Optional[str] = Field(default=None, max_length=255)


class WalletWithdraw(WalletTransactionBase):
    """Запрос на вывод."""

    reference: Optional[str] = Field(default=None, max_length=255)


class WalletTransfer(BaseModel):
    """Запрос на перевод между кошельками."""

    source_account_id: int = Field(..., ge=1)
    destination_account_id: int = Field(..., ge=1)
    amount: Decimal = Field(..., gt=0, description="Сумма перевода")
    reference: Optional[str] = Field(default=None, max_length=255)
    model_config = ConfigDict(json_encoders=_JSON_DECIMAL)

    @field_validator("amount", mode="before")
    @classmethod
    def _transfer_amount_to_decimal(cls, v: Any) -> Decimal:
        d = _to_decimal(v)
        if d <= 0:
            raise ValueError("amount must be > 0")
        return _quantize(d)


class WalletTransactionOut(WalletTransactionBase):
    """Ответ по транзакции."""

    id: int
    account_id: int
    type: TxType | str
    balance_after: Decimal
    created_at: datetime
    reference: Optional[str] = None
    # Для transfer удобно возвращать контекст
    source_account_id: Optional[int] = None
    destination_account_id: Optional[int] = None
    currency: Optional[str] = None

    @field_validator("balance_after", mode="before")
    @classmethod
    def _balance_after_to_decimal(cls, v: Any) -> Decimal:
        return _quantize(_to_decimal(v))

    @field_validator("currency", mode="before")
    @classmethod
    def _currency_norm_optional(cls, v: Any) -> Optional[str]:
        if v is None:
            return None
        return _normalize_currency(v)


# ====== Баланс ================================================================


class BalanceOut(BaseModel):
    """Единообразный ответ по балансу аккаунта."""

    account_id: int
    currency: str
    balance: Decimal
    model_config = ConfigDict(json_encoders=_JSON_DECIMAL)

    @field_validator("balance", mode="before")
    @classmethod
    def _balance_to_decimal_bal(cls, v: Any) -> Decimal:
        return _quantize(_to_decimal(v))

    @field_validator("currency", mode="before")
    @classmethod
    def _currency_norm_bal(cls, v: Any) -> str:
        return _normalize_currency(v)


# ====== Ледгер ================================================================


class LedgerItem(BaseModel):
    """Элемент выписки (ледгера)."""

    id: int
    account_id: int
    type: TxType | str
    amount: Decimal
    balance_after: Decimal
    currency: Optional[str] = None
    reference: Optional[str] = None
    created_at: datetime
    # контрагент/связь для переводов
    counterparty_account_id: Optional[int] = None
    payment_id: Optional[int] = None  # если операция связана с платёжкой

    model_config = ConfigDict(json_encoders=_JSON_DECIMAL)

    @field_validator("amount", "balance_after", mode="before")
    @classmethod
    def _money_to_decimal(cls, v: Any) -> Decimal:
        return _quantize(_to_decimal(v))

    @field_validator("currency", mode="before")
    @classmethod
    def _currency_norm_ledger(cls, v: Any) -> Optional[str]:
        if v is None:
            return None
        return _normalize_currency(v)


class LedgerPage(BaseModel):
    """Страница ледгера."""

    items: list[LedgerItem] = Field(default_factory=list)
    meta: PaginationMeta = Field(default_factory=PaginationMeta)


# ====== Универсальная ошибка (необязательно) =================================


class ErrorResponse(BaseModel):
    detail: str
    code: Optional[str] = None
    request_id: Optional[str] = None


# ====== Явный экспорт =========================================================

__all__ = [
    # аккаунты
    "WalletAccountBase",
    "WalletAccountCreate",
    "WalletAccountOut",
    "WalletAccountsPage",
    # операции
    "WalletDeposit",
    "WalletWithdraw",
    "WalletTransfer",
    "WalletTransactionOut",
    "TxType",
    # баланс
    "BalanceOut",
    # ледгер
    "LedgerItem",
    "LedgerPage",
    # пагинация
    "PaginationMeta",
    "Page",
    # ошибка
    "ErrorResponse",
]
