from ..extensions import db
from datetime import datetime

class AuditTrail(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer)
    action = db.Column(db.String(128))
    entity = db.Column(db.String(128))
    entity_id = db.Column(db.Integer)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    meta = db.Column(db.JSON)