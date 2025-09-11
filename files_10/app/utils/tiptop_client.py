import requests
import os

class TipTopClient:
    def __init__(self):
        self.public_id = os.getenv('TIPTOP_PUBLIC_ID')
        self.api_secret = os.getenv('TIPTOP_API_SECRET')
        self.base_url = "https://api.tiptop.kz/v1"

    def create_invoice(self, amount, order_id):
        payload = {
            "public_id": self.public_id,
            "amount": amount,
            "order_id": order_id,
        }
        r = requests.post(f"{self.base_url}/invoices", json=payload)
        return r.json()

    def refund(self, invoice_id, amount):
        payload = {"amount": amount}
        r = requests.post(f"{self.base_url}/invoices/{invoice_id}/refund", json=payload)
        return r.json()