class MockPayClient:
    def create_invoice(self, amount, order_id):
        return {"invoice_id": f"MOCK-{order_id}", "amount": amount, "status": "pending"}

    def refund(self, invoice_id, amount):
        return {"invoice_id": invoice_id, "amount": amount, "status": "refunded"}

    def verify_webhook(self, headers, body):
        return True