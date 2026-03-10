from __future__ import annotations

TENANT_STATE_ACTIVE = "active"
TENANT_STATE_ARCHIVED = "archived"
TENANT_STATE_DELETE_REQUESTED = "delete_requested"
TENANT_STATE_PENDING_EXPORT = "pending_export"
TENANT_STATE_PENDING_PURGE = "pending_purge"

_ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    TENANT_STATE_ACTIVE: {TENANT_STATE_ARCHIVED, TENANT_STATE_PENDING_EXPORT, TENANT_STATE_DELETE_REQUESTED},
    TENANT_STATE_ARCHIVED: {TENANT_STATE_ACTIVE, TENANT_STATE_PENDING_EXPORT, TENANT_STATE_DELETE_REQUESTED},
    TENANT_STATE_PENDING_EXPORT: {TENANT_STATE_DELETE_REQUESTED, TENANT_STATE_PENDING_PURGE},
    TENANT_STATE_DELETE_REQUESTED: {TENANT_STATE_PENDING_PURGE},
    TENANT_STATE_PENDING_PURGE: set(),
}


def is_transition_allowed(current_state: str, next_state: str) -> bool:
    current = (current_state or "").strip().lower()
    nxt = (next_state or "").strip().lower()
    if not current or not nxt:
        return False
    return nxt in _ALLOWED_TRANSITIONS.get(current, set())


def requires_export_before_delete() -> bool:
    return True


def can_request_delete(*, current_state: str, has_export_manifest_reference: bool = False) -> bool:
    if requires_export_before_delete() and not has_export_manifest_reference:
        return False
    return is_transition_allowed(current_state, TENANT_STATE_DELETE_REQUESTED)


def can_archive_tenant(*, current_state: str) -> bool:
    return is_transition_allowed(current_state, TENANT_STATE_ARCHIVED)


def infer_current_tenant_state(company) -> str:
    if getattr(company, "deleted_at", None) is not None:
        return TENANT_STATE_PENDING_PURGE
    if bool(getattr(company, "is_archived", False)):
        return TENANT_STATE_ARCHIVED
    return TENANT_STATE_ACTIVE


__all__ = [
    "TENANT_STATE_ACTIVE",
    "TENANT_STATE_ARCHIVED",
    "TENANT_STATE_DELETE_REQUESTED",
    "TENANT_STATE_PENDING_EXPORT",
    "TENANT_STATE_PENDING_PURGE",
    "is_transition_allowed",
    "requires_export_before_delete",
    "can_request_delete",
    "can_archive_tenant",
    "infer_current_tenant_state",
]
