"""Campus roles: coach (roll call, notifications, exercises for his classes), reception (visits, problems),
residence manager (SAS bedrooms / equipment). One logged-in user per test (Flask-Login caches it in `g`)."""
from datetime import timedelta

from app.models import AttendanceSession, Exercise, Incident, Notification, Sas, User, Visit
from app.services import campus_service
from app.utils import utcnow
from tests.conftest import login, make_candidate, make_user
from tests.test_portal import _logged_candidate


def _campus(db):
    """SAS 2026: ALPHA in Classe A, BETA in Classe B; coach of Classe A."""
    sas = Sas(name="SAS 2026")
    db.session.add(sas)
    db.session.commit()
    a = make_candidate(email="a@test.ci", cni="A", phone="0101010101", full_name="ALPHA", sas=sas, sas_class="Classe A")
    b = make_candidate(email="b@test.ci", cni="B", phone="0202020202", full_name="BETA", sas=sas, sas_class="Classe B")
    coach = make_user("coach", "coach@test.ci")
    coach.coach_classes = [f"{sas.id}:Classe A"]
    db.session.commit()
    return sas, a, b, coach


def test_admin_creates_coach_with_classes(app, db, client, admin):
    sas, *_ = _campus(db)
    login(client, admin)
    html = client.get("/admin/users/new").get_data(as_text=True)
    assert "Coach technique" in html and "Responsable accueil" in html and "Responsable de cité" in html
    assert f'value="{sas.id}:Classe B"' in html
    client.post("/admin/users/new", data={"email": "new@test.ci", "role": "coach", "password": "Password123!",
                                          "coach_classes": [f"{sas.id}:Classe A", f"{sas.id}:Classe B"]})
    assert User.query.filter_by(email="new@test.ci").one().coach_classes == [f"{sas.id}:Classe A", f"{sas.id}:Classe B"]
    client.post("/admin/users/new", data={"email": "rec@test.ci", "role": "reception", "password": "Password123!",
                                          "coach_classes": [f"{sas.id}:Classe A"]})
    assert User.query.filter_by(email="rec@test.ci").one().coach_classes == []  # classes: coaches only


def test_coach_works_on_his_classes_only(app, db, client):
    sas, a, b, coach = _campus(db)
    login(client, coach)
    assert client.get("/").headers["Location"].endswith("/campus/")
    assert client.get("/candidates/").status_code == 403 and client.get("/admin/users").status_code == 403
    html = client.get("/campus/").get_data(as_text=True)
    assert "Classe A" in html and "Classe B" not in html
    assert client.post("/campus/attendance", data={"target": f"{sas.id}:Classe B", "minutes": "10"},
                       follow_redirects=True).status_code == 200 and AttendanceSession.query.count() == 0  # refused
    resp = client.post("/campus/attendance", data={"target": f"{sas.id}:Classe A", "label": "Appel", "minutes": "10"})
    s = AttendanceSession.query.one()
    assert resp.headers["Location"].endswith(f"/campus/attendance/{s.id}") and s.class_name == "Classe A"
    other = AttendanceSession(sas=sas, class_name="Classe B", label="x", closes_at=utcnow() + timedelta(minutes=5))
    db.session.add(other)
    db.session.commit()
    assert client.get(f"/campus/attendance/{other.id}").status_code == 403

    client.post("/campus/notifications", data={"target": f"{sas.id}:Classe A", "title": "Cours annulé", "body": "Demain"})
    client.post("/campus/notifications", data={"target": f"{sas.id}:", "title": "Pour tous"})  # whole SAS: not a coach
    assert [n.title for n in Notification.query] == ["Cours annulé"]

    client.post("/campus/exercises", data={"target": f"{sas.id}:Classe A", "title": "Boucles", "instructions": "Écrire…"})
    e = Exercise.query.one()
    assert e.is_published
    html = client.get(f"/campus/exercises/{e.id}").get_data(as_text=True)
    assert "ALPHA" in html and "BETA" not in html
    client.post(f"/campus/exercises/{e.id}/review/{a.id}", data={"status": "validated", "score": "16/20", "feedback": "Bien"})
    sub = e.submissions[0]
    assert (sub.status, sub.score, sub.feedback) == ("validated", "16/20", "Bien")
    assert client.get("/campus/visits").status_code == 403  # reception only


def test_student_sees_notifications_and_scheduled_exercises(app, db, client):
    sas, a, b, coach = _campus(db)
    c = _logged_candidate(client, email="me@test.ci", cni="ME", phone="0303030303")
    c.sas, c.sas_class = sas, "Classe A"
    db.session.commit()
    campus_service.send_notification(coach, f"{sas.id}:Classe A", "Cours annulé", "Demain 8h")
    later = (utcnow() + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M")
    hidden = campus_service.create_exercise(coach, {"target": f"{sas.id}:Classe A", "title": "Demain", "opens_at": later})
    now = campus_service.create_exercise(coach, {"target": f"{sas.id}:Classe A", "title": "Boucles"})
    assert "Cours annulé" in client.get("/candidat/").get_data(as_text=True)
    html = client.get("/candidat/exercices").get_data(as_text=True)
    assert "Boucles" in html and "Demain" not in html  # scheduled: not visible yet
    assert client.post("/candidat/exercices", data={"exercise_id": hidden.id, "answer": "x"}).status_code == 404
    client.post("/candidat/exercices", data={"exercise_id": now.id, "answer": "for i in range(3): print(i)"})
    db.session.expire_all()
    assert now.submissions[0].answer.startswith("for i") and now.submissions[0].status == "submitted"
    campus_service.review(now, c, coach, "validated", "18", "Parfait")
    html = client.get("/candidat/exercices").get_data(as_text=True)
    assert "Validé" in html and "Parfait" in html and "Renvoyer" not in html


def test_reception_visits_and_problems(app, db, client):
    sas, a, b, coach = _campus(db)
    reception = make_user("reception", "rec@test.ci")
    login(client, reception)
    person = {"visitor_name": "Mme KOUAME", "origin": "Yamoussoukro", "id_type": "CNI", "id_number": "ci0012345"}
    post = lambda **form: client.post("/campus/visits", data={**person, **form},  # noqa: E731
                                      follow_redirects=True).get_data(as_text=True)
    post(kind="visit", student=f"{a.matricule} — ALPHA", reason="Colis")
    v = Visit.query.one()
    assert (v.kind, v.candidate_id, v.origin, v.id_number, v.left_at) == ("visit", a.id, "Yamoussoukro", "CI0012345", None)
    client.post(f"/campus/visits/{v.id}/leave")
    db.session.expire_all()
    assert v.left_at is not None
    recruit = make_candidate(email="r@test.ci", cni="R", phone="0404040404", full_name="RECRUE")  # not in a SAS
    post(kind="visit", student=recruit.matricule)
    assert Visit.query.filter_by(candidate_id=recruit.id).count() == 1  # candidates can be visited too
    post(kind="activity", activity_category="Livraison", activity="Ordinateurs", student=a.matricule)
    act = Visit.query.filter_by(kind="activity").one()
    assert (act.activity_category, act.activity, act.candidate_id) == ("Livraison", "Ordinateurs", None)
    page = client.get("/campus/visits").get_data(as_text=True)
    assert "Yamoussoukro" in page and "CI0012345" in page and "Livraison — Ordinateurs" in page

    assert "précisez le candidat" in post(kind="visit")
    assert "précisez la catégorie" in post(kind="activity", activity_category="Livraison")
    assert "Aucun candidat ou étudiant" in post(kind="visit", student="26CAND999999")
    html = client.post("/campus/visits", data={"kind": "visit", "visitor_name": "X", "student": a.matricule},
                       follow_redirects=True).get_data(as_text=True)
    assert "provenance, type de pièce, numéro de pièce" in html
    assert Visit.query.count() == 3

    # classes belong to the admin and the coaches
    assert client.get("/campus/classes").status_code == 403
    assert client.post("/campus/classes", data={"sas_id": sas.id, "name": "X"}).status_code == 403
    k = campus_service.get_class(sas.id, "Classe A")
    assert client.get(f"/campus/classes/{k.id}").status_code == 403
    assert "/campus/classes" not in client.get("/campus/").get_data(as_text=True)
    client.post("/campus/incidents", data={"student": a.matricule, "category": "health", "description": "Fièvre"})
    i = Incident.query.one()
    client.post(f"/campus/incidents/{i.id}/resolve", data={"resolution": "Vu par l'infirmière"})
    db.session.expire_all()
    assert i.resolved_at and i.resolution == "Vu par l'infirmière"
    assert "Fièvre" in client.get("/campus/incidents?status=resolved").get_data(as_text=True)


def test_coach_reports_problem_but_sees_only_his_own(app, db, client):
    sas, a, b, coach = _campus(db)
    db.session.add(Incident(candidate=b, description="Signalé par l'accueil", created_by=None))
    db.session.commit()
    login(client, coach)
    client.post("/campus/incidents", data={"student": a.email, "category": "discipline", "description": "Retards"})
    html = client.get("/campus/incidents").get_data(as_text=True)
    assert "Retards" in html and "Signalé par" not in html and "Suite donnée" not in html
    assert client.post(f"/campus/incidents/{Incident.query.first().id}/resolve").status_code == 403


def test_residence_manages_bedrooms_only(app, db, client):
    sas, a, b, coach = _campus(db)
    residence = make_user("residence", "cite@test.ci")
    login(client, residence)
    assert client.get("/").headers["Location"].endswith("/infrastructures/")
    html = client.get(f"/admin/sas/{sas.id}").get_data(as_text=True)
    assert "Chambre" in html and "Admettre des candidats" not in html and "Supprimer" not in html
    client.post(f"/admin/sas/{sas.id}/members/{a.id}", data={"class": "Classe A", "bedroom": "CH-01", "equipment": "Laptop"})
    db.session.expire_all()
    assert a.sas_bedroom == "CH-01"
    assert client.post(f"/admin/sas/{sas.id}/members/{a.id}", data={"remove": "1"}).status_code == 403
    for url in ("/admin/users", "/admin/rooms", f"/admin/sas/{sas.id}/delete", "/campus/visits", "/candidates/"):
        resp = client.post(url) if url.endswith("delete") else client.get(url)
        assert resp.status_code == 403, url


def test_reception_browses_sas_students(app, db, client):
    sas, a, b, coach = _campus(db)
    recruit = make_candidate(email="r@test.ci", cni="R", phone="0404040404", full_name="RECRUE")  # not in a SAS
    a.gender, a.sas_bedroom = "F", "CH-07"
    db.session.add(Incident(candidate=a, category="health", description="Allergie"))
    db.session.commit()
    login(client, make_user("reception", "rec@test.ci"))
    names = lambda qs: {n for n in ("ALPHA", "BETA", "RECRUE")  # noqa: E731
                        if n in client.get(f"/campus/students?{qs}").get_data(as_text=True)}
    assert names("") == {"ALPHA", "BETA"}  # SAS students only
    assert names("q=alp") == names(f"q={a.matricule}") == names("q=0101") == {"ALPHA"}
    assert names("class=Classe+B") == {"BETA"} and names("gender=F") == {"ALPHA"}
    html = client.get(f"/campus/students/{a.id}").get_data(as_text=True)
    assert all(t in html for t in ("ALPHA", a.matricule, "CH-07", "Classe A", "Allergie", "Heures d'absence"))
    assert "<form" not in html.split("<main", 1)[1].split("</main>")[0]  # read only
    assert client.get(f"/campus/students/{recruit.id}").status_code == 404


def test_students_pages_are_for_reception_and_admin(app, db, client):
    sas, a, b, coach = _campus(db)
    login(client, coach)
    assert client.get("/campus/students").status_code == 403
    assert client.get(f"/campus/students/{a.id}").status_code == 403


def test_admin_home_shows_sas_figures(app, db, client, admin):
    from datetime import timedelta

    from app.services import attendance_service, sas_service
    from app.utils import utcnow

    sas, a, b, coach = _campus(db)
    a.sas_bedroom = "C-101"
    db.session.commit()
    e = campus_service.create_exercise(coach, {"target": f"{sas.id}:Classe A", "title": "Boucles"})
    campus_service.submit_answer(e, a, "ma réponse")
    past = utcnow() - timedelta(hours=2)
    whole = attendance_service.open_session(sas, "", "Matin", 15, admin, starts_at=past, hours=2)  # ALPHA + BETA
    attendance_service.admin_set(whole, a, True, admin)
    attendance_service.open_session(sas, "Classe B", "Soir", 15, admin, starts_at=past, hours=3)  # BETA absent
    campus_service.add_incident(admin, {"student": a.matricule, "description": "Retard"})

    row = sas_service.dashboard_stats()["rows"][0]
    assert (row["students"], row["classes"], row["with_bedroom"], row["to_review"], row["calls"]) == (2, 2, 1, 1, 2)
    assert (row["absent_hours"], row["attendance_rate"], row["open_incidents"]) == (5, 33, 1)  # 1 present of 3
    login(client, admin)
    html = client.get("/").get_data(as_text=True)
    assert "Résumé par SAS" in html and "SAS 2026" in html and "5 h" in html and "33 %" in html
    assert "Résumé par SAS" not in client.get("/?partial=1").get_data(as_text=True)  # not polled
