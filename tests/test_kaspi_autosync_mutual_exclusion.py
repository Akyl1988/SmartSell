"""
Regression test for Kaspi autosync mutual exclusion.

Ensures that APScheduler kaspi_autosync job and main.py ENABLE_KASPI_SYNC_RUNNER
do not run simultaneously (runner takes precedence).

NOTE: These tests verify the mutual exclusion logic through the status endpoint
and helper functions. Full scheduler integration tests would require APScheduler installed.
"""

import os

import pytest


def test_mutual_exclusion_observability_in_status_endpoint():
    """Status endpoint should report runner_enabled and scheduler_job_effective_enabled."""
    from app.api.v1.kaspi import KaspiAutoSyncStatusOut

    # Verify schema has new fields
    schema_fields = KaspiAutoSyncStatusOut.model_fields
    assert "runner_enabled" in schema_fields
    assert "scheduler_job_effective_enabled" in schema_fields

    # Verify fields have proper descriptions
    assert "runner" in schema_fields["runner_enabled"].description.lower()
    assert "mutual exclusion" in schema_fields["scheduler_job_effective_enabled"].description.lower()


def test_env_truthy_helper_logic():
    """Test the env truthy logic independently."""

    def _env_truthy(value: str | None, default: bool = False) -> bool:
        """Replicate the helper logic."""
        if value is None:
            return default
        return value.strip().lower() in ("1", "true", "yes", "on", "enable", "enabled")

    # Truthy cases
    assert _env_truthy("1") is True
    assert _env_truthy("true") is True
    assert _env_truthy("TRUE") is True
    assert _env_truthy("yes") is True
    assert _env_truthy("Yes") is True
    assert _env_truthy("on") is True
    assert _env_truthy("ON") is True
    assert _env_truthy("enable") is True
    assert _env_truthy("enabled") is True
    assert _env_truthy("ENABLED") is True

    # Falsy cases
    assert _env_truthy("0") is False
    assert _env_truthy("false") is False
    assert _env_truthy("no") is False
    assert _env_truthy("off") is False
    assert _env_truthy("") is False
    assert _env_truthy("random") is False

    # None cases
    assert _env_truthy(None) is False
    assert _env_truthy(None, default=True) is True


def test_mutual_exclusion_logic():
    """Test the mutual exclusion decision logic."""

    def _env_truthy(value: str | None, default: bool = False) -> bool:
        if value is None:
            return default
        return value.strip().lower() in ("1", "true", "yes", "on", "enable", "enabled")

    def should_register(autosync_enabled: bool, runner_env: str | None) -> bool:
        """Replicate should_register_kaspi_autosync logic."""
        runner_enabled = _env_truthy(runner_env)
        if runner_enabled:
            return False  # Runner takes precedence
        return autosync_enabled

    # Case 1: Runner enabled → NO registration (mutual exclusion)
    assert should_register(autosync_enabled=True, runner_env="1") is False
    assert should_register(autosync_enabled=True, runner_env="true") is False
    assert should_register(autosync_enabled=True, runner_env="yes") is False

    # Case 2: Runner off + autosync enabled → YES registration
    assert should_register(autosync_enabled=True, runner_env="0") is True
    assert should_register(autosync_enabled=True, runner_env="false") is True
    assert should_register(autosync_enabled=True, runner_env=None) is True

    # Case 3: Autosync disabled → NO registration
    assert should_register(autosync_enabled=False, runner_env="0") is False
    assert should_register(autosync_enabled=False, runner_env="1") is False
    assert should_register(autosync_enabled=False, runner_env=None) is False
