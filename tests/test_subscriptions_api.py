import pytest
from decimal import Decimal
from sqlalchemy.exc import IntegrityError

from app.api.v1 import subscriptions as subs_api
from app.models.billing import BillingPayment, Subscription
from app.models.company import Company
from app.models.user import User


@pytest.mark.asyncio
async def test_forbid_multiple_active_exclude_id(async_db_session):
    company = Company.factory(name="ActiveGuard LLC")
    async_db_session.add(company)
    await async_db_session.commit()
    await async_db_session.refresh(company)

    sub1 = Subscription(
        company_id=company.id,
        plan="basic",
        status="active",
        billing_cycle="monthly",
        price=Decimal("10.00"),
        currency="KZT",
    )
    async_db_session.add(sub1)
    await async_db_session.commit()
    await async_db_session.refresh(sub1)

    # should not raise when exclude_id provided
    await subs_api.forbid_multiple_active(async_db_session, company.id, exclude_id=sub1.id)


@pytest.mark.asyncio
async def test_unique_active_subscription_guard(async_db_session):
    company = Company.factory(name="RaceGuard Inc")
    async_db_session.add(company)
    await async_db_session.commit()
    await async_db_session.refresh(company)

    sub_active = Subscription(
        company_id=company.id,
        plan="pro",
        status="active",
        billing_cycle="monthly",
        price=Decimal("20.00"),
        currency="KZT",
    )
    async_db_session.add(sub_active)
    await async_db_session.commit()

    sub_trial = Subscription(
        company_id=company.id,
        plan="pro",
        status="trial",
        billing_cycle="monthly",
        price=Decimal("0.00"),
        currency="KZT",
    )
    async_db_session.add(sub_trial)

    with pytest.raises(IntegrityError):
        await async_db_session.commit()
    await async_db_session.rollback()


@pytest.mark.asyncio
async def test_list_subscription_payments_isolated(async_db_session):
    company = Company.factory(name="LeakGuard LLC")
    async_db_session.add(company)
    await async_db_session.commit()
    await async_db_session.refresh(company)

    sub_a = Subscription(
        company_id=company.id,
        plan="alpha",
        status="active",
        billing_cycle="monthly",
        price=Decimal("10.00"),
        currency="KZT",
    )
    sub_b = Subscription(
        company_id=company.id,
        plan="beta",
        status="canceled",
        billing_cycle="monthly",
        price=Decimal("15.00"),
        currency="KZT",
    )
    async_db_session.add_all([sub_a, sub_b])
    await async_db_session.commit()
    await async_db_session.refresh(sub_a)
    await async_db_session.refresh(sub_b)

    pay_a = BillingPayment.factory(company_id=company.id, subscription_id=sub_a.id, amount=50)
    pay_b = BillingPayment.factory(company_id=company.id, subscription_id=sub_b.id, amount=75)
    async_db_session.add_all([pay_a, pay_b])
    await async_db_session.commit()
    await async_db_session.refresh(pay_a)
    await async_db_session.refresh(pay_b)

    user = User.factory(company_id=company.id, role="platform_admin")

    rows = await subs_api.list_subscription_payments(
        subscription_id=sub_a.id, db=async_db_session, user=user
    )

    ids = {p.id for p in rows}
    assert ids == {pay_a.id}


@pytest.mark.asyncio
async def test_final_statuses_visible(async_db_session):
    company = Company.factory(name="History LLC")
    async_db_session.add(company)
    await async_db_session.commit()
    await async_db_session.refresh(company)

    now = subs_api.utc_now()
    subs = [
        Subscription(
            company_id=company.id,
            plan="hist-a",
            status="canceled",
            billing_cycle="monthly",
            price=Decimal("5.00"),
            currency="KZT",
            canceled_at=now,
        ),
        Subscription(
            company_id=company.id,
            plan="hist-b",
            status="expired",
            billing_cycle="monthly",
            price=Decimal("6.00"),
            currency="KZT",
            expires_at=now,
        ),
        Subscription(
            company_id=company.id,
            plan="hist-c",
            status="ended",
            billing_cycle="monthly",
            price=Decimal("7.00"),
            currency="KZT",
            ended_at=now,
        ),
    ]
    async_db_session.add_all(subs)
    await async_db_session.commit()

    user = User.factory(company_id=company.id, role="platform_admin")
    rows = await subs_api.list_subscriptions(
        company_id=company.id,
        status_filter=None,
        plan=None,
        from_date=None,
        to_date=None,
        include_deleted=False,
        db=async_db_session,
        user=user,
    )
    ids = {s.plan for s in rows}
    assert ids.issuperset({"hist-a", "hist-b", "hist-c"})


@pytest.mark.asyncio
async def test_archive_and_restore_flow(async_db_session):
    company = Company.factory(name="Archive LLC")
    async_db_session.add(company)
    await async_db_session.commit()
    await async_db_session.refresh(company)

    sub = Subscription(
        company_id=company.id,
        plan="arch",
        status="active",
        billing_cycle="monthly",
        price=Decimal("9.00"),
        currency="KZT",
    )
    async_db_session.add(sub)
    await async_db_session.commit()
    await async_db_session.refresh(sub)

    admin = User.factory(role="platform_admin")

    archived = await subs_api.archive_subscription(sub.id, db=async_db_session, user=admin)
    assert archived.deleted_at is not None

    rows_visible = await subs_api.list_subscriptions(
        company_id=company.id,
        status_filter=None,
        plan=None,
        from_date=None,
        to_date=None,
        include_deleted=False,
        db=async_db_session,
        user=admin,
    )
    assert sub.id not in {s.id for s in rows_visible}

    rows_all = await subs_api.list_subscriptions(
        company_id=company.id,
        status_filter=None,
        plan=None,
        from_date=None,
        to_date=None,
        include_deleted=True,
        db=async_db_session,
        user=admin,
    )
    assert sub.id in {s.id for s in rows_all}

    current = await subs_api.get_current_subscription(
        company_id=company.id, db=async_db_session, user=admin
    )
    assert current is None

    restored = await subs_api.restore_subscription(sub.id, db=async_db_session, user=admin)
    assert restored.deleted_at is None

    rows_after_restore = await subs_api.list_subscriptions(
        company_id=company.id,
        status_filter=None,
        plan=None,
        from_date=None,
        to_date=None,
        include_deleted=False,
        db=async_db_session,
        user=admin,
    )
    assert sub.id in {s.id for s in rows_after_restore}


@pytest.mark.asyncio
async def test_active_uniqueness_ignores_archived(async_db_session):
    company = Company.factory(name="Unique LLC")
    async_db_session.add(company)
    await async_db_session.commit()
    await async_db_session.refresh(company)

    first = Subscription(
        company_id=company.id,
        plan="u1",
        status="active",
        billing_cycle="monthly",
        price=Decimal("10.00"),
        currency="KZT",
    )
    async_db_session.add(first)
    await async_db_session.commit()
    await async_db_session.refresh(first)

    admin = User.factory(role="platform_admin")
    await subs_api.archive_subscription(first.id, db=async_db_session, user=admin)

    second = Subscription(
        company_id=company.id,
        plan="u2",
        status="active",
        billing_cycle="monthly",
        price=Decimal("12.00"),
        currency="KZT",
    )
    async_db_session.add(second)

    await async_db_session.commit()
    await async_db_session.refresh(second)

    assert second.id is not None
