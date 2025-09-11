from flask import Blueprint, request, jsonify
from app.extensions import db
from app.models.product import Product
from app.models.hidden_product import HiddenProduct
from marshmallow import Schema, fields, ValidationError

product_actions_bp = Blueprint('product_actions', __name__, url_prefix='/api/product_actions')

class HideSchema(Schema):
    product_id = fields.Integer(required=True)

@product_actions_bp.route('/hide', methods=['POST'])
def hide_product():
    data = request.get_json()
    schema = HideSchema()
    try:
        validated = schema.load(data)
    except ValidationError as err:
        return {"error": "Ошибка валидации", "messages": err.messages}, 422

    product = Product.query.get(validated['product_id'])
    if not product:
        return {"error": "Товар не найден"}, 404
    product.is_hidden = True
    db.session.commit()
    hidden = HiddenProduct(product_id=product.id, hidden_by_user_id=1)
    db.session.add(hidden)
    db.session.commit()
    return {"success": True}

@product_actions_bp.route('/unhide', methods=['POST'])
def unhide_product():
    data = request.get_json()
    schema = HideSchema()
    try:
        validated = schema.load(data)
    except ValidationError as err:
        return {"error": "Ошибка валидации", "messages": err.messages}, 422

    product = Product.query.get(validated['product_id'])
    if not product:
        return {"error": "Товар не найден"}, 404
    product.is_hidden = False
    db.session.commit()
    HiddenProduct.query.filter_by(product_id=product.id).delete()
    db.session.commit()
    return {"success": True}