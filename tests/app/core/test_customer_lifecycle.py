from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from app.core.customer_lifecycle import CustomerLifecycleState, resolve_customer_lifecycle


def _company(*, archived: bool = False):
    return SimpleNamespace(is_archived=archived, deleted_at=None)


def _subscription(**kwargs):
    defaults = {
        "status": "active",
        "effective_status": "active",
        "deleted_at": None,
        "canceled_at": None,
        "frozen_at": None,
        "resumed_at": None,
        "period_end": datetime.now(UTC) + timedelta(days=30),
        "grace_until": None,
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def test_lifecycle_active_subscription_resolves_active():
    result = resolve_customer_lifecycle(company=_company(), subscription=_subscription())
    assert result.state == CustomerLifecycleState.ACTIVE
    assert result.reason == "subscription_active"


def test_lifecycle_expired_with_grace_resolves_grace():
    result = resolve_customer_lifecycle(
        company=_company(),
        subscription=_subscription(
            status="past_due",
            effective_status="overdue",
            grace_until=datetime.now(UTC) + timedelta(days=2),
            period_end=datetime.now(UTC) - timedelta(days=1),
        ),
    )
    assert result.state == CustomerLifecycleState.GRACE
    assert result.reason == "grace_period_active"


def test_lifecycle_suspended_resolves_suspended():
    result = resolve_customer_lifecycle(
        company=_company(),
        subscription=_subscription(
            status="paused",
            effective_status="paused",
            period_end=datetime.now(UTC) + timedelta(days=30),
        ),
    )
    assert result.state == CustomerLifecycleState.SUSPENDED
    assert result.reason == "subscription_suspended"


def test_lifecycle_no_subscription_resolves_churned():
    result = resolve_customer_lifecycle(company=_company(), subscription=None)
    assert result.state == CustomerLifecycleState.CHURNED
    assert result.reason == "no_subscription_record"
