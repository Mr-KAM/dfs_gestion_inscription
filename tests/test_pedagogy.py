"""Coach métier (invited into a class, creates none) and the coaches' program calendar with its follow-up."""
from app.models import AttendanceSession, CoachInvitation, Evaluation, Lesson, Sas, SasClass
from app.services import campus_service
from tests.conftest import login, make_candidate, make_user

LESSON = {"starts_at": "2026-12-07T09:00", "duration": "120", "title": "Lecture NDVI", "module": "Télédétection",
          "objectives": "Lire une carte NDVI", "repeat": "2"}


def test_trade_coach_invited_programs_but_does_not_evaluate(app, db, client, admin):
    sas = Sas(name="SAS 2026")
    db.session.add(sas)
    db.session.commit()
    coach, trade = make_user("coach", "coach@test.ci"), make_user("trade_coach", "tc@test.ci")
    k = campus_service.create_class(coach, sas.id, "Web")
    a = make_candidate(email="a@test.ci", cni="A", phone="0101010101", full_name="ALPHA", sas=sas, sas_class="Web")
    key = f"{sas.id}:Web"

    login(client, trade)
    assert "Nom de la classe" not in client.get("/campus/classes").get_data(as_text=True)  # no creation form
    client.post("/campus/classes", data={"sas_id": sas.id, "name": "Perso"})
    assert SasClass.query.count() == 1
    assert client.get(f"/campus/classes/{k.id}").status_code == 403  # not invited yet
    assert "Lecture seule" in client.get(f"/campus/students/{a.id}/evaluation").get_data(as_text=True)
    client.post("/logout")

    login(client, coach)
    client.post(f"/campus/classes/{k.id}/coaches", data={"user_id": trade.id})
    invitation = CoachInvitation.query.one()
    client.post("/logout")

    login(client, trade)
    assert "vous invite à intervenir" in client.get("/campus/").get_data(as_text=True)
    client.post(f"/campus/coach-invitations/{invitation.id}", data={"accept": "1"})
    db.session.expire_all()
    assert trade.coach_classes == [key] and client.get(f"/campus/classes/{k.id}").status_code == 200
    evaluation = f"/campus/students/{a.id}/evaluation"
    assert "Lecture seule" in client.get(evaluation).get_data(as_text=True)  # even in his class: coach technique only
    assert client.post(evaluation, data={"r_technicity": "3"}).status_code == 403 and Evaluation.query.count() == 0
    assert "Évaluer" not in client.get(f"/campus/classes/{k.id}").get_data(as_text=True)
    assert "Évaluer" not in client.get(f"/campus/students/{a.id}").get_data(as_text=True)
    other = make_user("trade_coach", "tc2@test.ci")
    html = client.post(f"/campus/classes/{k.id}/coaches", data={"user_id": other.id},
                       follow_redirects=True).get_data(as_text=True)
    assert "Seuls les coachs techniques" in html and CoachInvitation.query.count() == 1

    client.post("/campus/calendar", data={**LESSON, "target": key})
    lessons = Lesson.query.order_by(Lesson.starts_at).all()
    assert len(lessons) == 3 and {x.coach_id for x in lessons} == {trade.id}  # weekly, led by himself
    html = client.get("/campus/calendar?month=2026-12").get_data(as_text=True)
    assert html.count("Lecture NDVI") >= 3 and "4 h / 6 h" not in html
    first = lessons[0]
    client.post(f"/campus/lessons/{first.id}", data={**LESSON, "status": "done", "report": "Cartes lues"})
    assert "2 h / 6 h" in client.get("/campus/calendar?month=2026-12").get_data(as_text=True)
    resp = client.post(f"/campus/lessons/{first.id}/attendance", data={"minutes": "15", "hours": "2"})
    session = AttendanceSession.query.one()
    assert resp.headers["Location"].endswith(f"/campus/attendance/{session.id}")
    db.session.expire_all()
    assert (first.status, first.report, first.attendance_session_id, session.label) == (
        "done", "Cartes lues", session.id, "Lecture NDVI")
    client.post("/logout")

    login(client, coach)
    assert "Lecture NDVI" in client.get("/campus/calendar?month=2026-12").get_data(as_text=True)  # same class
    client.post(f"/campus/classes/{k.id}/coaches", data={"remove": trade.id})
    db.session.expire_all()
    assert trade.coach_classes == []
    client.post("/logout")
    login(client, trade)
    assert client.get(f"/campus/lessons/{first.id}").status_code == 403
    client.post("/logout")
    login(client, make_user("reception", "r@test.ci"))
    assert client.get("/campus/calendar").status_code == 403

    campus_service.rename_class(k, admin, "Web 2")
    assert {x.class_name for x in Lesson.query} == {"Web 2"}  # the calendar follows the class name
