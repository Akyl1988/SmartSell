from app.extensions import db

class PaymentHistory(db.Model):
    __tablename__ = 'payment_history'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    amount = db.Column(db.Integer)
    status = db.Column(db.String(32))
    provider = db.Column(db.String(32))
    created_at = db.Column(db.DateTime)
    invoice_id = db.Column(db.String(64))