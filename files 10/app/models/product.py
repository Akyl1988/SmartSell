from app.extensions import db

class Product(db.Model):
    __tablename__ = 'products'
    id = db.Column(db.Integer, primary_key=True)
    sku = db.Column(db.String(40), index=True)
    name = db.Column(db.String(128))
    price = db.Column(db.Integer)
    is_hidden = db.Column(db.Boolean, default=False)
    store_id = db.Column(db.Integer, db.ForeignKey('users.id'))