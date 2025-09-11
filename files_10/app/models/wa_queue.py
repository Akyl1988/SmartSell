from app.extensions import db
from sqlalchemy.dialects.postgresql import JSONB

class WAQueue(db.Model):
    __tablename__ = 'wa_queue'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    phone = db.Column(db.String(16))
    template_id = db.Column(db.String(32))
    payload = db.Column(JSONB)
    status = db.Column(db.String(16), default='queued')  # queued/sent/error
    scheduled_at = db.Column(db.DateTime)
    sent_at = db.Column(db.DateTime)
    tries = db.Column(db.Integer, default=0)
    error = db.Column(db.String(128))