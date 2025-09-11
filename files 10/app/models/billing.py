from app.extensions import db

class Billing(db.Model):
    __tablename__ = 'billing'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    invoice_id = db.Column(db.String(64))
    amount = db.Column(db.Integer)
    status = db.Column(db.String(32))
    created_at = db.Column(db.DateTime)
    updated_at = db.Column(db.DateTime)