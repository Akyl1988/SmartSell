from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from app.core.billing.state_machine import (
    BillingState,
    can_access_platform,
    can_transition,
    resolve_billing_state,
)


def _subscription(**overrides):
    base = {
        "status": None,
        "period_end": None,
        "expires_at": None,
        "grace_until": None,
        "canceled_at": None,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def test_trial_to_active_transition_allowed() -> None:
    assert can_transition(BillingState.TRIAL, BillingState.ACTIVE) is True

    now = datetime.now(UTC)
    trial_sub = _subscription(status="trialing", period_end=now + timedelta(days=3))
    active_sub = _subscription(status="active", period_end=now + timedelta(days=30))

    assert resolve_billing_state(trial_sub, now=now) == BillingState.TRIAL
    assert resolve_billing_state(active_sub, now=now) == BillingState.ACTIVE


def test_active_to_grace_resolution() -> None:
    now = datetime.now(UTC)
    sub = _subscription(
        status="active",
        period_end=now - timedelta(hours=1),
        grace_until=now + timedelta(days=7),
    )

    assert can_transition(BillingState.ACTIVE, BillingState.GRACE) is True
    assert resolve_billing_state(sub, now=now) == BillingState.GRACE


def test_grace_to_suspended_resolution() -> None:
    now = datetime.now(UTC)
    sub = _subscription(
        status="past_due",
        period_end=now - timedelta(days=2),
        grace_until=now - timedelta(minutes=1),
    )

    assert can_transition(BillingState.GRACE, BillingState.SUSPENDED) is True
    assert resolve_billing_state(sub, now=now) == BillingState.SUSPENDED


def test_suspended_access_denied() -> None:
    now = datetime.now(UTC)
    sub = _subscription(status="suspended")

    assert resolve_billing_state(sub, now=now) == BillingState.SUSPENDED
    assert can_access_platform(sub, now=now) is False
