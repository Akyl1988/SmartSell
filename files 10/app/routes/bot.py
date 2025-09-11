from flask import Blueprint, request, jsonify
from app.extensions import db
from app.models.bot_session import BotSession
from app.models.bot_history import BotHistory
from marshmallow import Schema, fields, ValidationError
from datetime import datetime

bot_bp = Blueprint('bot', __name__, url_prefix='/api/bot')

class StartBotSchema(Schema):
    user_id = fields.Integer(required=True)

@bot_bp.route('/start', methods=['POST'])
def start_bot():
    data = request.get_json()
    schema = StartBotSchema()
    try:
        validated = schema.load(data)
    except ValidationError as err:
        return {"error": "Ошибка валидации", "messages": err.messages}, 422

    session = BotSession(user_id=validated['user_id'], started_at=datetime.now(), status='active')
    db.session.add(session)
    db.session.commit()
    return {"success": True, "session_id": session.id}

@bot_bp.route('/history/<int:session_id>', methods=['GET'])
def get_history(session_id):
    history = BotHistory.query.filter_by(session_id=session_id).all()
    return jsonify([
        {"message": h.message, "created_at": h.created_at}
        for h in history
    ])