from flask import Blueprint, request, jsonify
from app.extensions import db
from app.models.billing import Billing
from app.models.tiptop_payment import TipTopPayment
from app.models.wallet import Wallet
from app.utils.payment_provider import get_payment_provider
from app.config import BaseConfig
from marshmallow import Schema, fields, ValidationError
import hmac
import hashlib

billing_bp = Blueprint('billing', __name__, url_prefix='/api/billing')

class BillingSchema(Schema):
    amount = fields.Integer(required=True)
    invoice_id = fields.String(required=True)

def check_hmac_signature(secret, body, signature):
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(digest, signature)

@billing_bp.route('/pay', methods=['POST'])
def create_billing():
    data = request.get_json()
    schema = BillingSchema()
    try:
        validated = schema.load(data)
    except ValidationError as err:
        return {"error": "Ошибка валидации", "messages": err.messages}, 422

    billing = Billing(
        user_id=1,  # Пример, в реальном проекте брать из JWT!
        invoice_id=validated['invoice_id'],
        amount=validated['amount'],
        status='pending',
    )
    db.session.add(billing)
    db.session.commit()
    return {"success": True, "billing_id": billing.id}

@billing_bp.route('/tiptop/webhook', methods=['POST'])
def tiptop_webhook():
    signature = request.headers.get("X-TipTop-Signature")
    body = request.data
    tiptop_id = request.json.get("tiptop_id")
    invoice_id = request.json.get("invoice_id")
    status = request.json.get("status")

    # Проверка идемпотентности
    payment = TipTopPayment.query.filter_by(tiptop_id=tiptop_id).first()
    if payment:
        return {"success": True, "msg": "Уже обработано"}, 200

    # Проверка подписи
    if not check_hmac_signature(BaseConfig.TIPTOP_API_SECRET, body, signature):
        return {"error": "Неверная подпись"}, 403

    # Обновление статуса платежа
    billing = Billing.query.filter_by(invoice_id=invoice_id).first()
    if not billing:
        return {"error": "Счет не найден"}, 404

    billing.status = status
    db.session.commit()
    tiptop_payment = TipTopPayment(
        billing_id=billing.id,
        tiptop_id=tiptop_id,
        status=status,
        hmac_signature=signature
    )
    db.session.add(tiptop_payment)
    db.session.commit()

    # Пополнение кошелька
    if status == "success":
        wallet = Wallet.query.filter_by(user_id=billing.user_id).first()
        if wallet:
            wallet.balance += billing.amount
            db.session.commit()
    return {"success": True}