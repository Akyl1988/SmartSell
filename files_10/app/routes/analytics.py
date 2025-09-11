from flask import Blueprint, request, jsonify
from app.extensions import db
from app.models.billing import Billing
from marshmallow import Schema, fields, ValidationError
from datetime import datetime

analytics_bp = Blueprint('analytics', __name__, url_prefix='/api/analytics')

class DateRangeSchema(Schema):
    start = fields.Date(required=True)
    end = fields.Date(required=True)

@analytics_bp.route('/billing', methods=['POST'])
def billing_analytics():
    data = request.get_json()
    schema = DateRangeSchema()
    try:
        dates = schema.load(data)
    except ValidationError as err:
        return {"error": "Ошибка валидации", "messages": err.messages}, 422

    start = datetime.combine(dates['start'], datetime.min.time())
    end = datetime.combine(dates['end'], datetime.max.time())
    billings = Billing.query.filter(Billing.created_at >= start, Billing.created_at <= end).all()
    total = sum(b.amount for b in billings)
    return {"total": total, "count": len(billings)}