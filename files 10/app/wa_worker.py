from app.extensions import db
from app.models.wa_queue import WAQueue
from datetime import datetime, time

def process_wa_queue():
    now = datetime.utcnow()
    wa_items = WAQueue.query.filter_by(status='pending').all()
    for item in wa_items:
        if item.scheduled_at.time() < time(8,0) or item.scheduled_at.time() > time(22,0):
            continue
        # Send logic here...
        item.status = 'sent'
        item.sent_at = datetime.utcnow()
        db.session.commit()