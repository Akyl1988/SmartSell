from __future__ import annotations

import pytest

from app.core.dependencies import require_roles
from app.core.exceptions import AuthorizationError


class _User:
    def __init__(self, role: str, is_superuser: bool = False):
        self.role = role
        self.is_superuser = is_superuser


@pytest.mark.asyncio
async def test_require_roles_allows_platform_admin():
    dep = require_roles("admin")
    user = _User("platform_admin")
    result = await dep(current_user=user)
    assert result is user


@pytest.mark.asyncio
async def test_require_roles_allows_admin():
    dep = require_roles("admin")
    user = _User("admin")
    result = await dep(current_user=user)
    assert result is user


@pytest.mark.asyncio
async def test_require_roles_blocks_manager():
    dep = require_roles("admin")
    user = _User("manager")
    with pytest.raises(AuthorizationError):
        await dep(current_user=user)
