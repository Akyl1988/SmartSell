from flask import Blueprint, request, jsonify
from app.extensions import db
from app.models.wallet import Wallet
from app.models.payment_history import PaymentHistory
from marshmallow import Schema, fields, ValidationError

wallet_bp = Blueprint('wallet', __name__, url_prefix='/api/wallet')

class TopupSchema(Schema):
    amount = fields.Integer(required=True)

@wallet_bp.route('/balance', methods=['GET'])
def get_balance():
    user_id = request.headers.get('X-User-Id', 1)
    wallet = Wallet.query.filter_by(user_id=user_id).first()
    return {"balance": wallet.balance if wallet else 0}

@wallet_bp.route('/topup', methods=['POST'])
def wallet_topup():
    data = request.get_json()
    schema = TopupSchema()
    try:
        validated = schema.load(data)
    except ValidationError as err:
        return {"error": "Ошибка валидации", "messages": err.messages}, 422

    user_id = request.headers.get('X-User-Id', 1)
    wallet = Wallet.query.filter_by(user_id=user_id).first()
    if not wallet:
        wallet = Wallet(user_id=user_id, balance=0)
        db.session.add(wallet)
    wallet.balance += validated['amount']
    db.session.commit()

    history = PaymentHistory(user_id=user_id, amount=validated['amount'], status='success', provider='manual')
    db.session.add(history)
    db.session.commit()
    return {"success": True, "balance": wallet.balance}

@wallet_bp.route('/history', methods=['GET'])
def wallet_history():
    user_id = request.headers.get('X-User-Id', 1)
    history = PaymentHistory.query.filter_by(user_id=user_id).order_by(PaymentHistory.created_at.desc()).limit(20).all()
    return jsonify([
        {"amount": h.amount, "status": h.status, "provider": h.provider, "created_at": h.created_at}
        for h in history
    ])