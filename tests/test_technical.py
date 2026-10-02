from datetime import timedelta

import pytest
from sqlalchemy.exc import IntegrityError

from app.models import AuditLog, Candidate, TechnicalTestSession
from app.services import timer_service
from app.services.candidate_service import BusinessError
from app.utils import utcnow
from tests.conftest import eligible_candidate, login, make_candidate


def start(candidate, room, tester, ws_index=0, minutes=60):
    return timer_service.start_test(candidate.id, room.id, room.workstations[ws_index].id, minutes, tester)


def test_start_test(app, db, room, technical_user):
    c = eligible_candidate()
    s = start(c, room, technical_user)
    assert s.status == "running" and s.duration_seconds == 3600 and s.started_at
    assert 3595 <= s.remaining_seconds() <= 3600
    assert {"workstation_assign", "test_start"} <= {a.action for a in db.session.query(AuditLog)}


def test_cannot_start_without_motivation(app, room, technical_user):
    with pytest.raises(BusinessError, match="motivation"):
        start(make_candidate(), room, technical_user)


def test_requires_room_and_workstation(app, room, technical_user):
    c = eligible_candidate()
    with pytest.raises(BusinessError, match="salle"):
        timer_service.start_test(c.id, None, room.workstations[0].id, 60, technical_user)
    with pytest.raises(BusinessError, match="ordinateur"):
        timer_service.start_test(c.id, room.id, None, 60, technical_user)


def test_workstation_already_occupied(app, room, technical_user):
    first = eligible_candidate()
    second = eligible_candidate(email="b@t.ci", cni="B", exam_code="DFS-2026-0002")
    start(first, room, technical_user)
    with pytest.raises(BusinessError, match="actuellement utilisé"):
        start(second, room, technical_user)
    start(second, room, technical_user, ws_index=1)  # another PC is fine


def test_candidate_cannot_have_two_active_sessions(app, room, technical_user):
    c = eligible_candidate()
    start(c, room, technical_user)
    with pytest.raises(BusinessError, match="déjà un test"):
        start(c, room, technical_user, ws_index=1)


def test_database_blocks_double_booking(app, db, room, technical_user):
    """Rule 5 holds even if application checks are bypassed."""
    a = eligible_candidate()
    b = eligible_candidate(email="b@t.ci", cni="B", exam_code="DFS-2026-0002")
    ws = room.workstations[0]
    for c in (a, b):
        db.session.add(TechnicalTestSession(candidate_id=c.id, room_id=room.id, workstation_id=ws.id,
                                            duration_seconds=60, status="running", started_at=utcnow()))
    with pytest.raises(IntegrityError):
        db.session.commit()
    db.session.rollback()


def test_pause_resume_accounting(app, db, room, technical_user):
    s = start(eligible_candidate(), room, technical_user)
    s.started_at = utcnow() - timedelta(minutes=10)  # simulate 10 minutes of test
    db.session.commit()
    timer_service.pause(s.id)
    s.paused_at = utcnow() - timedelta(minutes=5)  # paused for the last 5 minutes
    db.session.commit()
    assert abs(s.remaining_seconds() - 55 * 60) <= 2  # countdown frozen during pause
    timer_service.resume(s.id)
    assert s.status == "running" and 299 <= s.total_pause_seconds <= 301
    assert abs(s.remaining_seconds() - 55 * 60) <= 2  # 60 - (10 - 5)
    with pytest.raises(BusinessError):
        timer_service.resume(s.id)


def test_stop_frees_workstation(app, db, room, technical_user):
    c = eligible_candidate()
    s = start(c, room, technical_user)
    timer_service.stop(s.id)
    assert s.status == "stopped" and s.finished_at
    assert not db.session.get(Candidate, c.id).technical_completed
    other = eligible_candidate(email="b@t.ci", cni="B", exam_code="DFS-2026-0002")
    start(other, room, technical_user)  # PC is free again


def test_complete_marks_candidate_and_frees_workstation(app, db, room, technical_user):
    c = eligible_candidate()
    s = start(c, room, technical_user)
    timer_service.pause(s.id)
    timer_service.complete(s.id)
    c = db.session.get(Candidate, c.id)
    assert s.status == "completed" and s.technical_completed and s.paused_at is None
    assert c.technical_completed and c.technical_completed_at
    assert c.global_status == "Technique terminée"
    assert room.workstations[0].id not in timer_service.occupied_sessions_by_workstation()
    with pytest.raises(BusinessError, match="déjà terminé"):
        start(c, room, technical_user, ws_index=1)


def test_expired_timer_shows_zero(app, db, room, technical_user):
    s = start(eligible_candidate(), room, technical_user, minutes=1)
    s.started_at = utcnow() - timedelta(minutes=5)
    db.session.commit()
    assert s.remaining_seconds() == 0


def test_http_flow_and_refresh_keeps_timer(client, db, room, technical_user):
    c = eligible_candidate()
    login(client, technical_user)
    ws = room.workstations[2]
    free = client.get(f"/technical/rooms/{room.id}/free.json").get_json()
    assert ws.id in [w["id"] for w in free]

    response = client.post(f"/technical/start/{c.id}",
                           data={"room_id": room.id, "workstation_id": ws.id, "duration_minutes": 60},
                           follow_redirects=True)
    assert "TEMPS ÉCOULÉ" in response.get_data(as_text=True)  # live panel rendered (label hidden until 0)
    assert ws.id not in [w["id"] for w in client.get(f"/technical/rooms/{room.id}/free.json").get_json()]
    rooms_page = client.get("/technical/rooms?partial=1").get_data(as_text=True)
    assert "Occupé" in rooms_page and "DFS-2026-0001" in rooms_page

    session = db.session.query(TechnicalTestSession).one()
    session.started_at = utcnow() - timedelta(minutes=30)
    db.session.commit()
    panel = client.get(f"/technical/candidate/{c.id}/panel").get_data(as_text=True)  # "page refresh"
    assert 'data-remaining="17' in panel or 'data-remaining="18' in panel  # ~1800 s left, from the DB

    client.post(f"/technical/session/{session.id}/complete")
    assert ws.id in [w["id"] for w in client.get(f"/technical/rooms/{room.id}/free.json").get_json()]
