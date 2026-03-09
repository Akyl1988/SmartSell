from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from typing import Any


class BillingState(str, Enum):
    TRIAL = "trial"
    ACTIVE = "active"
    GRACE = "grace"
    SUSPENDED = "suspended"
    CANCELLED = "cancelled"


_ALLOWED_TRANSITIONS: set[tuple[BillingState, BillingState]] = {
    (BillingState.TRIAL, BillingState.ACTIVE),
    (BillingState.TRIAL, BillingState.CANCELLED),
    (BillingState.ACTIVE, BillingState.GRACE),
    (BillingState.ACTIVE, BillingState.CANCELLED),
    (BillingState.GRACE, BillingState.ACTIVE),
    (BillingState.GRACE, BillingState.SUSPENDED),
    (BillingState.GRACE, BillingState.CANCELLED),
    (BillingState.SUSPENDED, BillingState.ACTIVE),
    (BillingState.SUSPENDED, BillingState.CANCELLED),
}


_STATUS_ALIASES = {
    "trial": BillingState.TRIAL,
    "trialing": BillingState.TRIAL,
    "active": BillingState.ACTIVE,
    "grace": BillingState.GRACE,
    "past_due": BillingState.GRACE,
    "overdue": BillingState.GRACE,
    "suspended": BillingState.SUSPENDED,
    "paused": BillingState.SUSPENDED,
    "frozen": BillingState.SUSPENDED,
    "canceled": BillingState.CANCELLED,
    "cancelled": BillingState.CANCELLED,
    "expired": BillingState.CANCELLED,
}


@dataclass(frozen=True)
class SubscriptionSnapshot:
    status: str | None
    period_end: datetime | None
    expires_at: datetime | None
    grace_until: datetime | None
    canceled_at: datetime | None


def _normalize_status(status: str | None) -> BillingState | None:
    key = (status or "").strip().lower()
    if not key:
        return None
    return _STATUS_ALIASES.get(key)


def _to_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _is_past(dt: datetime | None, now: datetime) -> bool:
    point = _to_utc(dt)
    return point is not None and point <= now


def _is_future(dt: datetime | None, now: datetime) -> bool:
    point = _to_utc(dt)
    return point is not None and point > now


def _snapshot(subscription: Any | None) -> SubscriptionSnapshot:
    if subscription is None:
        return SubscriptionSnapshot(None, None, None, None, None)
    return SubscriptionSnapshot(
        status=getattr(subscription, "status", None),
        period_end=getattr(subscription, "period_end", None),
        expires_at=getattr(subscription, "expires_at", None),
        grace_until=getattr(subscription, "grace_until", None),
        canceled_at=getattr(subscription, "canceled_at", None),
    )


def can_transition(current: BillingState, target: BillingState) -> bool:
    if current == target:
        return True
    return (current, target) in _ALLOWED_TRANSITIONS


def resolve_billing_state(subscription: Any | None, now: datetime | None = None) -> BillingState:
    snapshot = _snapshot(subscription)
    current_time = _to_utc(now) or datetime.now(UTC)

    if snapshot.canceled_at is not None:
        return BillingState.CANCELLED

    normalized = _normalize_status(snapshot.status)
    expiry = snapshot.period_end or snapshot.expires_at

    if normalized == BillingState.CANCELLED:
        return BillingState.CANCELLED
    if normalized == BillingState.SUSPENDED:
        return BillingState.SUSPENDED
    if normalized == BillingState.TRIAL:
        if _is_past(expiry, current_time):
            return BillingState.GRACE if _is_future(snapshot.grace_until, current_time) else BillingState.CANCELLED
        return BillingState.TRIAL
    if normalized == BillingState.GRACE:
        return BillingState.GRACE if _is_future(snapshot.grace_until, current_time) else BillingState.SUSPENDED
    if normalized == BillingState.ACTIVE:
        if _is_past(expiry, current_time):
            return BillingState.GRACE if _is_future(snapshot.grace_until, current_time) else BillingState.SUSPENDED
        return BillingState.ACTIVE

    if _is_future(snapshot.grace_until, current_time):
        return BillingState.GRACE
    if _is_past(expiry, current_time):
        return BillingState.SUSPENDED
    return BillingState.ACTIVE


def is_in_grace(subscription: Any | None, now: datetime | None = None) -> bool:
    return resolve_billing_state(subscription, now=now) == BillingState.GRACE


def is_suspended(subscription: Any | None, now: datetime | None = None) -> bool:
    return resolve_billing_state(subscription, now=now) == BillingState.SUSPENDED


def can_access_platform(subscription: Any | None, now: datetime | None = None) -> bool:
    state = resolve_billing_state(subscription, now=now)
    return state in {BillingState.TRIAL, BillingState.ACTIVE, BillingState.GRACE}


__all__ = [
    "BillingState",
    "can_transition",
    "resolve_billing_state",
    "can_access_platform",
    "is_in_grace",
    "is_suspended",
]
