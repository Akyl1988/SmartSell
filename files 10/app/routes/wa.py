from flask import Blueprint, request, jsonify
from app.extensions import db
from app.models.wa_queue import WAQueue
from app.config import BaseConfig
from apscheduler.schedulers.background import BackgroundScheduler
from datetime import datetime, time
from marshmallow import Schema, fields, ValidationError

wa_bp = Blueprint('wa', __name__, url_prefix='/api/wa')
scheduler = BackgroundScheduler()

class SendWASchema(Schema):
    phone = fields.String(required=True)
    template_id = fields.String(required=True)
    payload = fields.Dict(required=True)

def is_night(now):
    start = time(BaseConfig.NIGHT_START, 0)
    end = time(BaseConfig.NIGHT_END, 0)
    return now.time() >= start or now.time() < end

def daily_limit_exceeded(user_id):
    today = datetime.now().date()
    count = WAQueue.query.filter_by(user_id=user_id).filter(
        WAQueue.scheduled_at >= datetime.combine(today, time(0, 0))
    ).count()
    return count >= BaseConfig.WA_DAILY_LIMIT

def wa_worker():
    now = datetime.now()
    if is_night(now):
        return
    queue = WAQueue.query.filter_by(status='queued').filter(WAQueue.scheduled_at <= now).all()
    for item in queue:
        # Имитация отправки WA, ошибки/логика
        try:
            item.status = 'sent'
            item.sent_at = now
            item.error = None
        except Exception as e:
            item.status = 'error'
            item.error = str(e)
        finally:
            item.tries += 1
            db.session.commit()

scheduler.add_job(wa_worker, 'interval', minutes=1)
scheduler.start()

@wa_bp.route('/send', methods=['POST'])
def send_wa():
    data = request.get_json()
    schema = SendWASchema()
    try:
        validated = schema.load(data)
    except ValidationError as err:
        return {"error": "Ошибка валидации", "messages": err.messages}, 422

    user_id = request.headers.get('X-User-Id', 1)  # В реальном приложении — из JWT!
    if daily_limit_exceeded(user_id):
        return {"error": "Дневной лимит WA превышен"}, 429
    if is_night(datetime.now()):
        return {"error": "Ночная пауза отправки WA"}, 429

    wa = WAQueue(
        user_id=user_id,
        phone=validated['phone'],
        template_id=validated['template_id'],
        payload=validated['payload'],
        status='queued',
        scheduled_at=datetime.now()
    )
    db.session.add(wa)
    db.session.commit()
    return {"success": True, "id": wa.id}

@wa_bp.route('/status', methods=['GET'])
def wa_status():
    user_id = request.headers.get('X-User-Id', 1)
    messages = WAQueue.query.filter_by(user_id=user_id).order_by(WAQueue.scheduled_at.desc()).limit(10).all()
    return jsonify([
        {
            "id": m.id,
            "status": m.status,
            "tries": m.tries,
            "error": m.error,
            "scheduled_at": m.scheduled_at,
            "sent_at": m.sent_at
        }
        for m in messages
    ])