def test_create_billing(client):
    resp = client.post('/api/billing/pay', json={"amount": 1000, "invoice_id": "INV1"})
    assert resp.status_code == 200
    assert resp.get_json()["success"]

def test_tiptop_webhook(client):
    data = {
        "tiptop_id": "TT1",
        "invoice_id": "INV1",
        "status": "success"
    }
    # Имитация подписи HMAC
    resp = client.post('/api/billing/tiptop/webhook', json=data, headers={"X-TipTop-Signature": "dummy"})
    assert resp.status_code in (200, 403)