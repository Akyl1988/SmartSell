from marshmallow import Schema, fields

class ProductSchema(Schema):
    name = fields.Str(required=True)
    price = fields.Float(required=True)
    stock = fields.Int(required=True)
    photo_url = fields.Str()
    store_id = fields.Int()
    external_id = fields.Str()