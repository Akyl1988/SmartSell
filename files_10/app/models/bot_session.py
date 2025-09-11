from app.extensions import db

class BotSession(db.Model):
    __tablename__ = 'bot_sessions'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    started_at = db.Column(db.DateTime)
    status = db.Column(db.String(32))