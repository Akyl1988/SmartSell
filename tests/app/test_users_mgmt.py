import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import create_access_token, get_password_hash
from app.models import Company, User, UserSession


async def _make_user(
    async_db_session: AsyncSession,
    *,
    company: Company,
    phone: str,
    role: str,
    email: str | None = None,
    is_active: bool = True,
) -> User:
    user = User(
        company_id=company.id,
        phone=phone,
        email=email,
        hashed_password=get_password_hash("Secret123!"),
        role=role,
        is_active=is_active,
        is_verified=True,
    )
    async_db_session.add(user)
    await async_db_session.commit()
    await async_db_session.refresh(user)
    return user


def _auth_headers(user: User) -> dict[str, str]:
    token = create_access_token(subject=user.id, extra={"company_id": user.company_id, "role": user.role})
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_list_users_owner_and_admin_allowed_employee_forbidden(
    async_client: AsyncClient, async_db_session: AsyncSession
):
    company = Company(name="Users Co")
    async_db_session.add(company)
    await async_db_session.flush()

    owner = await _make_user(async_db_session, company=company, phone="77000011111", role="admin")
    company.owner_id = owner.id
    await async_db_session.commit()

    admin = await _make_user(async_db_session, company=company, phone="77000011112", role="admin")
    employee = await _make_user(async_db_session, company=company, phone="77000011113", role="employee")

    resp_owner = await async_client.get("/api/v1/users", headers=_auth_headers(owner))
    assert resp_owner.status_code == 200
    assert len(resp_owner.json().get("items") or []) >= 3

    resp_admin = await async_client.get("/api/v1/users", headers=_auth_headers(admin))
    assert resp_admin.status_code == 200

    resp_emp = await async_client.get("/api/v1/users", headers=_auth_headers(employee))
    assert resp_emp.status_code == 403


@pytest.mark.asyncio
async def test_deactivate_rules_and_session_invalidation(async_client: AsyncClient, async_db_session: AsyncSession):
    company = Company(name="Deactivate Co")
    async_db_session.add(company)
    await async_db_session.flush()

    owner = await _make_user(async_db_session, company=company, phone="77000022221", role="admin")
    company.owner_id = owner.id
    await async_db_session.commit()

    admin = await _make_user(async_db_session, company=company, phone="77000022222", role="admin")
    employee = await _make_user(async_db_session, company=company, phone="77000022223", role="employee")
    admin_peer = await _make_user(async_db_session, company=company, phone="77000022224", role="admin")
    admin_id = admin.id
    employee_id = employee.id
    admin_peer_id = admin_peer.id
    owner_id = owner.id
    admin_headers = _auth_headers(admin)
    owner_headers = _auth_headers(owner)

    session = UserSession(user_id=employee_id, refresh_token="x", is_active=True)
    async_db_session.add(session)
    await async_db_session.commit()

    resp_admin = await async_client.post(f"/api/v1/users/{employee_id}/deactivate", headers=admin_headers)
    assert resp_admin.status_code == 200

    resp_owner = await async_client.post(f"/api/v1/users/{admin_id}/deactivate", headers=owner_headers)
    assert resp_owner.status_code == 200

    resp_activate = await async_client.post(f"/api/v1/users/{admin_id}/activate", headers=owner_headers)
    assert resp_activate.status_code == 200

    await async_db_session.rollback()
    res_admin = await async_db_session.execute(select(User).where(User.id == admin_id))
    admin_refreshed = res_admin.scalars().first()
    assert admin_refreshed is not None and admin_refreshed.is_active is True

    await async_db_session.rollback()
    res_session = await async_db_session.execute(select(UserSession).where(UserSession.user_id == employee_id))
    sess = res_session.scalars().first()
    assert sess is not None and sess.is_active is False

    resp_admin_bad = await async_client.post(f"/api/v1/users/{admin_peer_id}/deactivate", headers=admin_headers)
    assert resp_admin_bad.status_code == 403

    resp_owner_bad = await async_client.post(f"/api/v1/users/{owner_id}/deactivate", headers=owner_headers)
    assert resp_owner_bad.status_code == 422


@pytest.mark.asyncio
async def test_role_change_rules_and_tenant_isolation(async_client: AsyncClient, async_db_session: AsyncSession):
    company_a = Company(name="Roles A")
    company_b = Company(name="Roles B")
    async_db_session.add(company_a)
    async_db_session.add(company_b)
    await async_db_session.flush()

    owner = await _make_user(async_db_session, company=company_a, phone="77000033331", role="admin")
    company_a.owner_id = owner.id
    await async_db_session.commit()

    admin = await _make_user(async_db_session, company=company_a, phone="77000033332", role="admin")
    employee = await _make_user(async_db_session, company=company_a, phone="77000033333", role="employee")
    other_company_user = await _make_user(async_db_session, company=company_b, phone="77000033334", role="employee")

    resp_owner = await async_client.post(
        f"/api/v1/users/{employee.id}/role",
        headers=_auth_headers(owner),
        json={"role": "admin"},
    )
    assert resp_owner.status_code == 200

    resp_admin = await async_client.post(
        f"/api/v1/users/{employee.id}/role",
        headers=_auth_headers(admin),
        json={"role": "admin"},
    )
    assert resp_admin.status_code == 403

    resp_self = await async_client.post(
        f"/api/v1/users/{admin.id}/role",
        headers=_auth_headers(admin),
        json={"role": "manager"},
    )
    assert resp_self.status_code == 422

    resp_other = await async_client.post(
        f"/api/v1/users/{other_company_user.id}/role",
        headers=_auth_headers(owner),
        json={"role": "employee"},
    )
    assert resp_other.status_code == 404
