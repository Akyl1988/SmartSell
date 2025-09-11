from flask import Blueprint, request, jsonify
from app.extensions import db
from app.models.product import Product
from marshmallow import Schema, fields, ValidationError

import_export_bp = Blueprint('import_export', __name__, url_prefix='/api/import_export')

class ProductSchema(Schema):
    sku = fields.String(required=True)
    name = fields.String(required=True)
    price = fields.Integer(required=True)

@import_export_bp.route('/import', methods=['POST'])
def import_products():
    data = request.get_json()
    schema = ProductSchema(many=True)
    try:
        products = schema.load(data)
    except ValidationError as err:
        return {"error": "Ошибка валидации", "messages": err.messages}, 422

    for p in products:
        product = Product(sku=p['sku'], name=p['name'], price=p['price'])
        db.session.add(product)
    db.session.commit()
    return {"success": True, "count": len(products)}

@import_export_bp.route('/export', methods=['GET'])
def export_products():
    products = Product.query.all()
    schema = ProductSchema(many=True)
    return jsonify(schema.dump(products))