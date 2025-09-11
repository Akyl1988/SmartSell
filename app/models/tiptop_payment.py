from app.extensions import db

class TipTopPayment(db.Model):
    __tablename__ = 'tiptop_payments'
    id = db.Column(db.Integer, primary_key=True)
    billing_id = db.Column(db.Integer, db.ForeignKey('billing.id'))
    tiptop_id = db.Column(db.String(64))
    status = db.Column(db.String(32))
    created_at = db.Column(db.DateTime)
    hmac_signature = db.Column(db.String(128))