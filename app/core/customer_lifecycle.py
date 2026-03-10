from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from app.core.subscriptions.state import is_subscription_active


class CustomerLifecycleState(StrEnum):
    TRIAL = "TRIAL"
    ACTIVE = "ACTIVE"
    GRACE = "GRACE"
    SUSPENDED = "SUSPENDED"
    RECOVERED = "RECOVERED"
    CHURNED = "CHURNED"
    ARCHIVED = "ARCHIVED"


@dataclass(frozen=True)
class CustomerLifecycleResolution:
    state: CustomerLifecycleState
    reason: str
    source: str


def _norm(value: str | None) -> str:
    return (value or "").strip().lower()


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _is_future(value: datetime | None, now: datetime) -> bool:
    ts = _as_utc(value)
    return bool(ts and ts > now)


def resolve_customer_lifecycle(
    *,
    company: Any | None,
    subscription: Any | None,
    now: datetime | None = None,
) -> CustomerLifecycleResolution:
    """
    Resolve operational customer lifecycle from existing company/subscription context.

    Notes:
    - Uses current persisted signals only (company archive/deletion, subscription status/effective_status/grace/resume).
    - RECOVERED is inferred only when an explicit resume signal exists (`resumed_at`).
    - No historical event timeline is available here, so richer recovery/churn segmentation is intentionally deferred.
    """

    current_time = _as_utc(now) or datetime.now(UTC)

    if company is not None:
        is_archived = bool(getattr(company, "is_archived", False))
        is_deleted = getattr(company, "deleted_at", None) is not None
        if is_archived or is_deleted:
            return CustomerLifecycleResolution(
                state=CustomerLifecycleState.ARCHIVED,
                reason="company_archived",
                source="company.is_archived",
            )

    if subscription is None or getattr(subscription, "deleted_at", None) is not None:
        return CustomerLifecycleResolution(
            state=CustomerLifecycleState.CHURNED,
            reason="no_subscription_record",
            source="subscription.absent",
        )

    status = _norm(getattr(subscription, "status", None))
    effective_status = _norm(getattr(subscription, "effective_status", None))
    grace_until = getattr(subscription, "grace_until", None)
    if grace_until is None:
        grace_expires_fn = getattr(subscription, "grace_expires_at", None)
        if callable(grace_expires_fn):
            try:
                grace_until = grace_expires_fn()
            except Exception:
                grace_until = None

    if status in {"paused", "frozen"} or effective_status == "paused":
        return CustomerLifecycleResolution(
            state=CustomerLifecycleState.SUSPENDED,
            reason="subscription_suspended",
            source="subscription.status",
        )

    if (status in {"overdue", "past_due"} or effective_status in {"overdue", "past_due"}) and _is_future(
        grace_until, current_time
    ):
        return CustomerLifecycleResolution(
            state=CustomerLifecycleState.GRACE,
            reason="grace_period_active",
            source="subscription.grace_until",
        )

    if status in {"trial", "trialing"} or effective_status in {"trial", "trialing"}:
        return CustomerLifecycleResolution(
            state=CustomerLifecycleState.TRIAL,
            reason="trial_subscription",
            source="subscription.effective_status",
        )

    resumed_at = _as_utc(getattr(subscription, "resumed_at", None))
    frozen_at = _as_utc(getattr(subscription, "frozen_at", None))
    has_resume_signal = resumed_at is not None and (frozen_at is None or resumed_at >= frozen_at)
    if has_resume_signal and is_subscription_active(subscription, now=current_time):
        return CustomerLifecycleResolution(
            state=CustomerLifecycleState.RECOVERED,
            reason="resumed_after_suspension",
            source="subscription.resumed_at",
        )

    if is_subscription_active(subscription, now=current_time):
        return CustomerLifecycleResolution(
            state=CustomerLifecycleState.ACTIVE,
            reason="subscription_active",
            source="subscriptions.state.is_subscription_active",
        )

    if status in {"canceled", "cancelled"} or effective_status in {"canceled", "cancelled", "expired"}:
        return CustomerLifecycleResolution(
            state=CustomerLifecycleState.CHURNED,
            reason="subscription_ended",
            source="subscription.effective_status",
        )

    return CustomerLifecycleResolution(
        state=CustomerLifecycleState.CHURNED,
        reason="no_valid_subscription_state",
        source="subscription.status",
    )


__all__ = [
    "CustomerLifecycleState",
    "CustomerLifecycleResolution",
    "resolve_customer_lifecycle",
]
