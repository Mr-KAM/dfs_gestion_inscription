"""Classes: a coach creates a class, students join it by QR code / address / code, or accept an invitation."""
from app.models import ClassInvitation, Sas, SasClass, User
from app.services import campus_service
from tests.conftest import login, make_candidate, make_user
from tests.test_portal import _logged_candidate


def _setup(db):
    sas, other = Sas(name="SAS 2026"), Sas(name="SAS 2027")
    db.session.add_all([sas, other])
    db.session.commit()
    a = make_candidate(email="a@test.ci", cni="A", phone="0101010101", full_name="ALPHA", sas=sas)
    out = make_candidate(email="o@test.ci", cni="O", phone="0202020202", full_name="OUT", sas=other)
    coach = make_user("coach", "coach@test.ci")
    return sas, other, a, out, coach


def test_coach_creates_class_and_invites(app, db, client):
    sas, other, a, out, coach = _setup(db)
    login(client, coach)
    resp = client.post("/campus/classes", data={"sas_id": sas.id, "name": "  Dev   Web "})
    k = SasClass.query.one()
    assert k.name == "Dev Web" and len(k.join_code) == 8 and resp.headers["Location"].endswith(f"/campus/classes/{k.id}")
    assert db.session.get(User, coach.id).coach_classes == [f"{sas.id}:Dev Web"]  # now one of his classes
    assert "Dev Web" in client.get("/campus/classes").get_data(as_text=True)  # listed though still empty
    html = client.get(f"/campus/classes/{k.id}").get_data(as_text=True)
    assert "<svg" in html and k.join_code in html and f"/candidat/classe?code={k.join_code}" in html
    html = client.post("/campus/classes", data={"sas_id": sas.id, "name": "Dev Web"}, follow_redirects=True).get_data(as_text=True)
    assert "existe déjà" in html and SasClass.query.count() == 1

    html = client.post(f"/campus/classes/{k.id}/invite", data={"candidates": f"{a.matricule.lower()}\n{out.email}\ninconnu@x.ci"},
                       follow_redirects=True).get_data(as_text=True)
    assert "1 invitation(s)" in html and "pas admis dans SAS 2026" in html and "inconnu@x.ci : introuvable" in html
    assert [(i.candidate_id, i.status) for i in ClassInvitation.query] == [(a.id, "pending")]
    html = client.post(f"/campus/classes/{k.id}/invite", data={"candidates": a.email}, follow_redirects=True).get_data(as_text=True)
    assert "déjà invité" in html
    html = client.get(f"/campus/classes/{k.id}").get_data(as_text=True)
    assert "Date heure invitation" in html and "ALPHA" in html and "En cours" in html  # invited, not yet in

    old = k.join_code
    client.post(f"/campus/classes/{k.id}/renew")
    db.session.expire_all()
    assert k.join_code != old

    other_class = campus_service.get_class(other.id, "Autre")
    assert client.get(f"/campus/classes/{other_class.id}").status_code == 403  # not his class


def test_student_accepts_invitation(app, db, client):
    sas, other, a, out, coach = _setup(db)
    c = _logged_candidate(client, email="me@test.ci", cni="ME", phone="0303030303")
    c.sas = sas
    db.session.commit()
    k = campus_service.create_class(coach, sas.id, "Dev Web")
    campus_service.invite(k, coach, c.matricule)
    html = client.get("/candidat/").get_data(as_text=True)
    assert "vous invite à rejoindre" in html and "Dev Web" in html
    invitation = ClassInvitation.query.one()
    client.post(f"/candidat/invitations/{invitation.id}", data={"accept": "1", "next": "/candidat/"})
    db.session.expire_all()
    assert c.sas_class == "Dev Web" and invitation.status == "accepted"
    assert "vous invite" not in client.get("/candidat/").get_data(as_text=True)
    k2 = campus_service.create_class(coach, sas.id, "Data")
    campus_service.invite(k2, coach, c.email)
    client.post(f"/candidat/invitations/{ClassInvitation.query.filter_by(class_id=k2.id).one().id}", data={"accept": "0"})
    db.session.expire_all()
    assert c.sas_class == "Dev Web"  # declined: stays in his class
    assert client.post(f"/candidat/invitations/{invitation.id}", data={"accept": "1"},
                       follow_redirects=True).status_code == 200  # already answered: refused, no crash


def test_student_joins_by_code_or_address(app, db, client):
    sas, other, a, out, coach = _setup(db)
    c = _logged_candidate(client, email="me@test.ci", cni="ME", phone="0303030303")
    c.sas = sas
    db.session.commit()
    k = campus_service.create_class(coach, sas.id, "Dev Web")
    resp = client.get(f"/candidat/classe?code={k.join_code}")  # phone camera opened the QR code address
    assert "Rejoindre la classe" in resp.get_data(as_text=True) and "camera=(self)" in resp.headers["Permissions-Policy"]
    html = client.post("/candidat/classe", data={"token": f"https://dfs.example/candidat/classe?code={k.join_code}"},
                       follow_redirects=True).get_data(as_text=True)
    assert "Vous avez rejoint la classe « Dev Web »" in html
    k2 = campus_service.create_class(coach, sas.id, "Data")
    client.post("/candidat/classe", data={"token": f"  {k2.join_code.lower()} "})  # typed by hand
    db.session.expire_all()
    assert c.sas_class == "Data"
    foreign = campus_service.create_class(coach, other.id, "Ailleurs")
    html = client.post("/candidat/classe", data={"token": foreign.join_code}, follow_redirects=True).get_data(as_text=True)
    assert "vous n" in html and "admis" in html
    html = client.post("/candidat/classe", data={"token": "ZZZZZZZZ"}, follow_redirects=True).get_data(as_text=True)
    assert "Code de classe inconnu" in html
    db.session.expire_all()
    assert c.sas_class == "Data"


def test_admin_sees_classes_of_every_coach_with_exercises(app, db, client, admin):
    from datetime import timedelta

    from app.services import attendance_service
    from app.utils import utcnow

    sas, other, a, out, coach = _setup(db)
    coach.full_name = "Coach Yao"
    other_coach = make_user("coach", "coach2@test.ci")
    k = campus_service.create_class(coach, sas.id, "Dev Web")
    campus_service.create_class(other_coach, sas.id, "Data")
    a.sas_class = "Dev Web"
    db.session.commit()
    e = campus_service.create_exercise(coach, {"target": f"{sas.id}:Dev Web", "title": "Boucles"})
    campus_service.create_exercise(coach, {"target": f"{sas.id}:Dev Web", "title": "Fonctions"})
    campus_service.submit_answer(e, a, "ma réponse")
    campus_service.send_notification(coach, f"{sas.id}:Dev Web", "Cours annulé", "")
    attendance_service.open_session(sas, "Dev Web", "Lundi", 15, coach, starts_at=utcnow() - timedelta(hours=2), hours=3)

    login(client, admin)
    html = client.get("/campus/classes").get_data(as_text=True)
    assert "Coach Yao" in html and "Dev Web" in html and "Data" in html and "Classes et coachs" in html
    html = client.get(f"/campus/classes?coach={other_coach.id}").get_data(as_text=True)
    assert "Data" in html and "Dev Web" not in html
    html = client.get(f"/campus/classes/{k.id}").get_data(as_text=True)
    assert all(t in html for t in ("Boucles", "Fonctions", "1 / 1", "Cours annulé", "Lundi", "Coach Yao", "3 h"))
    report = campus_service.class_report(k)
    boucles = next(x for x in report["exercises"] if x["exercise"].title == "Boucles")
    assert (boucles["handed_in"], boucles["to_review"], report["absent_hours"]) == (1, 1, 3)  # A missed the 3 h call
    campus_service.review(e, a, coach, "validated", "", "")
    row = campus_service.class_report(k)["roster"][0]
    assert (row["candidate"].id, row["member"], row["validated"]) == (a.id, True, 1)
    assert "Rejoint par code" in client.get(f"/campus/classes/{k.id}").get_data(as_text=True)
    report = campus_service.class_report(k)
    assert (report["roster"][0]["absent_hours"], report["roster"][0]["presence"]) == (3, 0)
    assert report["stats"] == {"presence": 0, "handed_in": 50, "validation": 50, "absentees": 1,
                               "pending_invitations": 0}  # 1 student, 2 exercises: 1 handed in and validated

    def page(**args):
        return client.get(f"/campus/classes/{k.id}", query_string=args).get_data(as_text=True)
    assert "Taux de présence" in page() and "50 %" in page()
    assert "Boucles" in page(eq="bou") and "Fonctions" not in page(eq="bou")
    assert "Boucles" not in page(est="review") and "Fonctions" not in page(est="review")  # all reviewed
    assert "Lundi" in page(cst="closed") and "Lundi" not in page(cst="open") and "Lundi" not in page(cq="mardi")
    assert "ALPHA" in page(abs="with") and "ALPHA" not in page(abs="without")
    assert '<input type="hidden" name="eq" value="bou">' in page(eq="bou", abs="with")  # other lists keep it
    b = make_candidate(email="b@test.ci", cni="B", phone="0404040404", full_name="BRAVO", sas=sas)
    campus_service.invite(k, coach, b.matricule)

    def names(**args):  # students listed once the roster is filtered
        html = client.get(f"/campus/classes/{k.id}", query_string=args).get_data(as_text=True)
        return [n for n in ("ALPHA", "BRAVO") if n in html]
    assert names() == ["ALPHA", "BRAVO"]
    assert names(q="bra") == ["BRAVO"] and names(q=a.matricule.lower()) == ["ALPHA"]
    assert names(state="pending") == ["BRAVO"] and names(state="code") == ["ALPHA"]
    assert names(done="some") == ["ALPHA"] and names(done="none") == ["BRAVO"] and names(done="all") == []  # 1 of 2
