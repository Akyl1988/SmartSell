from app.extensions import db

class BotHistory(db.Model):
    __tablename__ = 'bot_history'
    id = db.Column(db.Integer, primary_key=True)
    session_id = db.Column(db.Integer, db.ForeignKey('bot_sessions.id'))
    message = db.Column(db.String(256))
    created_at = db.Column(db.DateTime)