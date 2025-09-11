import pytest
from app.models.wa_queue import WAQueue

def test_daily_limit(client, db_session):
    # setup user, create 800 messages
    for i in range(800):
        wa = WAQueue(user_id=1, phone=f"+700000000{i}", template_id=1)
        db_session.add(wa)
    db_session.commit()
    # try to send 801st
    response = client.post("/api/wa/send", json={
        "user_id": 1,
        "phone": "+7000000801",
        "template_id": 1
    })
    assert response.status_code == 429
    assert response.json["error"] == "daily WA limit reached"