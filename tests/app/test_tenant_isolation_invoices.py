import pytest


BASE = "/api/v1/invoices"


@pytest.mark.anyio
async def test_invoices_list_isolated_between_companies(async_client, company_a_admin_headers, company_b_admin_headers):
    payload = {"amount": "15.00", "currency": "KZT", "status": "draft", "description": "tenant A invoice"}
    created = await async_client.post(BASE, json=payload, headers=company_a_admin_headers)
    assert created.status_code == 201, created.text
    invoice_id = created.json()["id"]

    # company B must not see company A invoice in list
    list_b = await async_client.get(BASE, headers=company_b_admin_headers)
    assert list_b.status_code == 200, list_b.text
    items = list_b.json() or []
    assert all(it.get("id") != invoice_id for it in items)


@pytest.mark.anyio
async def test_invoice_get_by_id_hidden_from_other_company(async_client, company_a_admin_headers, company_b_admin_headers):
    payload = {"amount": "42.50", "currency": "KZT", "status": "draft", "description": "secret"}
    created = await async_client.post(BASE, json=payload, headers=company_a_admin_headers)
    assert created.status_code == 201, created.text
    invoice_id = created.json()["id"]

    # company B cannot fetch invoice of company A
    got = await async_client.get(f"{BASE}/{invoice_id}", headers=company_b_admin_headers)
    assert got.status_code == 404, got.text
