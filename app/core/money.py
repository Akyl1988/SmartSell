from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

KZT_CURRENCY = "KZT"


class MoneyNormalizationError(ValueError):
    pass


def _to_decimal(value: Any) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise MoneyNormalizationError("invalid decimal value") from exc


def normalize_money(amount: Any, currency: str | None, *, non_kzt_places: int = 2) -> Decimal:
    d = _to_decimal(amount)
    cur = (currency or "").strip().upper()
    if cur == KZT_CURRENCY:
        q = d.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        if q != d:
            raise MoneyNormalizationError("KZT amount must be an integer")
        return q

    if non_kzt_places < 0:
        return d
    step = Decimal(1) / (Decimal(10) ** non_kzt_places)
    return d.quantize(step, rounding=ROUND_HALF_UP)


def format_money(amount: Any, currency: str | None, *, non_kzt_places: int = 2) -> str:
    d = normalize_money(amount, currency, non_kzt_places=non_kzt_places)
    cur = (currency or "").strip().upper()
    if cur == KZT_CURRENCY:
        return str(int(d))
    return str(d.normalize()) if d == d.to_integral() else str(d)


def is_kzt(currency: str | None) -> bool:
    return (currency or "").strip().upper() == KZT_CURRENCY


__all__ = ["KZT_CURRENCY", "MoneyNormalizationError", "format_money", "is_kzt", "normalize_money"]
