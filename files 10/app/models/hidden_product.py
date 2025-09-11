from ..extensions import db
from datetime import datetime

class HiddenProduct(db.Model):
    id = db.Column(db.Integer, db.ForeignKey('product.id'), primary_key=True)
    hidden_at = db.Column(db.DateTime, default=datetime.utcnow)
    restored_at = db.Column(db.DateTime)