class PaymentProvider:
    def create_invoice(self, amount, order_id):
        raise NotImplementedError

    def refund(self, invoice_id, amount):
        raise NotImplementedError

    def verify_webhook(self, headers, body):
        raise NotImplementedError

def get_payment_provider():
    # Можно выбрать mock или tiptop по ENV (упрощено)
    from app.utils.mock_pay_client import MockPayClient
    return MockPayClient()