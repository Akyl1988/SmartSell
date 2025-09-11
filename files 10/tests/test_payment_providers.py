from app.utils.mock_pay_client import MockPayClient

def test_mock_create_invoice():
    client = MockPayClient()
    result = client.create_invoice(1000, "ORDER1")
    assert result["status"] == "pending"
    assert result["invoice_id"].startswith("MOCK-")