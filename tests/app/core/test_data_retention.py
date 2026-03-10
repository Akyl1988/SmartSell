from __future__ import annotations

from app.core.data_retention import (
    RETENTION_CAMPAIGNS_DAYS,
    RETENTION_DIAGNOSTICS_SNAPSHOTS_DAYS,
    RETENTION_EVENTS_DAYS,
    RETENTION_LOGS_DAYS,
    RETENTION_ORDERS_DAYS,
    RETENTION_POLICY_VERSION,
    RETENTION_REPORTS_DAYS,
    get_retention_limits,
)


def test_data_retention_config_loads() -> None:
    assert RETENTION_POLICY_VERSION
    assert RETENTION_ORDERS_DAYS > 0
    assert RETENTION_CAMPAIGNS_DAYS > 0
    assert RETENTION_LOGS_DAYS > 0
    assert RETENTION_EVENTS_DAYS > 0
    assert RETENTION_REPORTS_DAYS > 0
    assert RETENTION_DIAGNOSTICS_SNAPSHOTS_DAYS > 0

    limits = get_retention_limits()
    assert limits["orders_days"] == RETENTION_ORDERS_DAYS
    assert limits["campaigns_days"] == RETENTION_CAMPAIGNS_DAYS
    assert limits["logs_days"] == RETENTION_LOGS_DAYS
    assert limits["events_days"] == RETENTION_EVENTS_DAYS
    assert limits["reports_days"] == RETENTION_REPORTS_DAYS
    assert limits["diagnostics_snapshots_days"] == RETENTION_DIAGNOSTICS_SNAPSHOTS_DAYS
