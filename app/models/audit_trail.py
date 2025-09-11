from app.extensions import db

class AuditTrail(db.Model):
    __tablename__ = 'audit_trail'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    action = db.Column(db.String(128))
    details = db.Column(db.String(256))
    created_at = db.Column(db.DateTime)