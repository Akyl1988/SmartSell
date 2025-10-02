# app/models/types.py
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Iterable, Optional, Sequence

from sqlalchemy.types import TypeDecorator, JSON, DateTime, String
from sqlalchemy import types as sqltypes

try:
    # Будет доступен только если установлен драйвер/диалект PostgreSQL
    from sqlalchemy.dialects.postgresql import JSONB as PG_JSONB  # type: ignore
except Exception:  # pragma: no cover
    PG_JSONB = None  # type: ignore


# ======================================================================
# JSONBCompat — кросс-СУБД совместимый JSONB
# ======================================================================


class JSONBCompat(TypeDecorator):
    """
    Кросс-СУБД тип "JSONB":
    - В PostgreSQL → настоящий JSONB
    - В остальных (SQLite, MySQL) → обычный JSON

    Решает ошибку компиляции:
        "can't render element of type JSONB" под SQLite.

    Использование:
        payload: Mapped[dict | None] = mapped_column(JSONBCompat)
    """

    impl = JSON  # базовая реализация для не-PG
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if PG_JSONB is not None and dialect.name == "postgresql":
            return dialect.type_descriptor(PG_JSONB())
        return dialect.type_descriptor(JSON())


# ======================================================================
# UTCDateTime — хранение и возврат времени в UTC
# ======================================================================


class UTCDateTime(TypeDecorator):
    """
    Приводит datetime к UTC при записи и возвращает aware-дату в UTC при чтении.

    - Если пришла naive-дата → считаем, что это UTC.
    - Если aware-дата → конвертируем к UTC.
    - В хранилище кладём naive UTC (совместимо с SQLite).
    - При чтении возвращаем aware UTC.

    Использование:
        created_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    """

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: Optional[datetime], dialect) -> Optional[datetime]:
        if value is None:
            return None
        if value.tzinfo is None:
            # считаем, что это уже UTC
            return value.replace(tzinfo=None)
        # конвертируем в UTC и убираем tzinfo для совместимости с большинством БД
        return value.astimezone(timezone.utc).replace(tzinfo=None)

    def process_result_value(self, value: Optional[datetime], dialect) -> Optional[datetime]:
        if value is None:
            return None
        # возвращаем aware-дату в UTC
        return value.replace(tzinfo=timezone.utc)


# ======================================================================
# TrimmedString / LowercaseString — нормализация строк
# ======================================================================


class TrimmedString(TypeDecorator):
    """
    Строковый тип, который:
      - обрезает пробелы по краям,
      - приводит пустую строку к NULL,
      - опционально делает lowercase.

    Аргументы:
      - length: int | None — ограничение длины (как у String)
      - lowercase: bool — привести к нижнему регистру

    Использование:
        name: Mapped[str | None] = mapped_column(TrimmedString(255))
        slug: Mapped[str | None] = mapped_column(TrimmedString(64, lowercase=True))
    """

    impl = String
    cache_ok = True

    def __init__(self, length: Optional[int] = None, *, lowercase: bool = False) -> None:
        super().__init__(length)
        self._lowercase = bool(lowercase)

    def process_bind_param(self, value: Optional[str], dialect) -> Optional[str]:
        if value is None:
            return None
        v = value.strip()
        if not v:
            return None
        if self._lowercase:
            v = v.lower()
        # String сам обрежет по длине на уровне БД; здесь не режем.
        return v


class LowercaseString(TrimmedString):
    """Удобный алиас: TrimmedString(..., lowercase=True)"""

    def __init__(self, length: Optional[int] = None) -> None:
        super().__init__(length, lowercase=True)


# ======================================================================
# CurrencyCode — валидация 3-10 симв. кода валюты (обычно 3 — ISO 4217)
# ======================================================================


class CurrencyCode(TypeDecorator):
    """
    Валютный код (по умолчанию разрешаем 3..10 символов, только A-Z/0-9/_-).
    Приводит к верхнему регистру, пустые строки → NULL.

    Использование:
        currency: Mapped[str | None] = mapped_column(CurrencyCode())
    """

    impl = String(10)
    cache_ok = True
    _re = re.compile(r"^[A-Z0-9_\-]{3,10}$")

    def process_bind_param(self, value: Optional[str], dialect) -> Optional[str]:
        if value is None:
            return None
        v = value.strip().upper()
        if not v:
            return None
        if not self._re.match(v):
            raise ValueError(f"Invalid currency code: {value!r}")
        return v


# ======================================================================
# ChoiceString — безопасное хранение ограниченного множества значений
# ======================================================================


class ChoiceString(TypeDecorator):
    """
    Ограничивает значение набором допустимых строк (case-sensitive по умолчанию).
    По желанию может приводить к нижнему/верхнему регистру для нормализации.

    Аргументы:
      choices: Iterable[str] — допустимые значения (на уровне Python)
      normalize: Literal['lower', 'upper', None] — нормализация перед проверкой
      length: int — длина базового String

    Использование:
        status: Mapped[str | None] = mapped_column(ChoiceString(
            choices={"active", "paused", "canceled"}, normalize="lower", length=16
        ))
    """

    impl = String
    cache_ok = True

    def __init__(
        self,
        *,
        choices: Iterable[str],
        normalize: Optional[str] = None,
        length: int = 32,
    ) -> None:
        super().__init__(length)
        self._normalize = normalize if normalize in ("lower", "upper", None) else None
        self._choices_raw: Sequence[str] = tuple(choices)
        self._choices_norm: set[str] = set(self._norm(v) for v in self._choices_raw)

    def _norm(self, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        if self._normalize == "lower":
            return v.lower()
        if self._normalize == "upper":
            return v.upper()
        return v

    def process_bind_param(self, value: Optional[str], dialect) -> Optional[str]:
        if value is None:
            return None
        v = value.strip()
        if not v:
            return None
        v_norm = self._norm(v)
        if v_norm not in self._choices_norm:
            allowed = ", ".join(sorted(self._choices_raw))
            raise ValueError(f"Invalid choice: {value!r}. Allowed: {allowed}")
        # В БД кладём так, как пришло после нормализации (если была)
        return v_norm


# ======================================================================
# Экспорт
# ======================================================================

__all__ = [
    "JSONBCompat",
    "UTCDateTime",
    "TrimmedString",
    "LowercaseString",
    "CurrencyCode",
    "ChoiceString",
]
