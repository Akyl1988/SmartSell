import pytest


@pytest.mark.asyncio
async def test_campaigns_isolation_between_companies(
    client,
    company_a_admin_headers,
    company_b_admin_headers,
):
    created = await client.post(
        "/api/v1/campaigns/",
        json={
            "title": "Company B Campaign",
            "description": "Tenant isolation check",
            "messages": [
                {
                    "recipient": "b@example.com",
                    "content": "hi",
                    "status": "pending",
                    "channel": "email",
                }
            ],
            "tags": ["b"],
            "active": True,
        },
        headers=company_b_admin_headers,
    )
    assert created.status_code == 201, created.text
    campaign_id = created.json()["id"]

    foreign_get = await client.get(f"/api/v1/campaigns/{campaign_id}", headers=company_a_admin_headers)
    assert foreign_get.status_code == 404

    listed = await client.get("/api/v1/campaigns/", headers=company_a_admin_headers)
    assert listed.status_code == 200, listed.text
    body = listed.json()
    items = body["items"] if isinstance(body, dict) and "items" in body else body
    assert all(item.get("id") != campaign_id for item in items)

    messages = await client.get(
        f"/api/v1/campaigns/{campaign_id}/messages",
        headers=company_a_admin_headers,
    )
    assert messages.status_code == 404


@pytest.mark.asyncio
async def test_campaign_messages_not_mutable_cross_company(
    client,
    company_a_admin_headers,
    company_b_admin_headers,
):
    created = await client.post(
        "/api/v1/campaigns/",
        json={
            "title": "Company B Campaign 2",
            "description": "Tenant isolation message mutation",
            "messages": [],
            "tags": ["b"],
            "active": True,
        },
        headers=company_b_admin_headers,
    )
    assert created.status_code == 201, created.text
    campaign_id = created.json()["id"]

    add_message = await client.post(
        f"/api/v1/campaigns/{campaign_id}/messages",
        json={
            "recipient": "a@example.com",
            "content": "cross-tenant",
            "status": "pending",
            "channel": "email",
        },
        headers=company_a_admin_headers,
    )
    assert add_message.status_code == 404

    bulk_status = await client.post(
        f"/api/v1/campaigns/{campaign_id}/messages/bulk_status_update",
        json={"ids": [123], "status": "sent"},
        headers=company_a_admin_headers,
    )
    assert bulk_status.status_code == 404
