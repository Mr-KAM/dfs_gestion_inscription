"""SAS defenses: juries, their members (one jury per person and defense) and the students they hear."""
from app.models import Defense, Jury, JuryCandidate, JuryMember, Sas
from tests.conftest import login, make_candidate, make_user


def test_admin_schedules_defense_with_juries(app, db, client, admin):
    sas, other = Sas(name="SAS 2026"), Sas(name="SAS 2027")
    db.session.add_all([sas, other])
    db.session.commit()
    a = make_candidate(email="a@test.ci", cni="A", phone="0101010101", full_name="ALPHA", sas=sas, sas_class="Web")
    b = make_candidate(email="b@test.ci", cni="B", phone="0202020202", full_name="BRAVO", sas=sas, sas_class="Web")
    make_candidate(email="c@test.ci", cni="C", phone="0303030303", full_name="CHARLIE", sas=sas)
    out = make_candidate(email="o@test.ci", cni="O", phone="0404040404", full_name="OUT", sas=other)
    coach, tester = make_user("coach", "coach@test.ci"), make_user("technical_tester", "t@test.ci")

    login(client, admin)
    client.post("/soutenances/", data={"sas_id": sas.id, "name": "Soutenance finale",
                                       "starts_at": "2026-12-01T08:00", "location": "Amphi"})
    d = Defense.query.one()
    for name in ("Jury 1", "Jury 2"):
        client.post(f"/soutenances/{d.id}/juries", data={"name": name, "room": "S1", "duration": "20"})
    j1, j2 = Jury.query.order_by(Jury.id).all()
    assert client.post(f"/soutenances/{d.id}/juries", data={"name": "X", "duration": "2"},
                       follow_redirects=True).get_data(as_text=True).count("entre 5 et 480") == 1

    client.post(f"/soutenances/juries/{j1.id}/members", data={"user_id": coach.id, "role": "president"})
    html = client.post(f"/soutenances/juries/{j2.id}/members", data={"user_id": coach.id, "role": "technical"},
                       follow_redirects=True).get_data(as_text=True)
    assert "déjà dans le jury « Jury 1 »" in html  # one jury per person in a defense
    client.post(f"/soutenances/juries/{j2.id}/members", data={"user_id": tester.id, "role": "presentation"})
    assert {(m.jury_id, m.user_id, m.role) for m in JuryMember.query} == {(j1.id, coach.id, "president"),
                                                                         (j2.id, tester.id, "presentation")}
    m = JuryMember.query.filter_by(user_id=tester.id).one()
    client.post(f"/soutenances/members/{m.id}", data={"role": "technical"})
    db.session.expire_all()
    assert m.role == "technical"

    html = client.post(f"/soutenances/juries/{j1.id}/candidates",
                       data={"candidates": f"{b.matricule.lower()}\n{out.email}\nINCONNU", "class": "Web"},
                       follow_redirects=True).get_data(as_text=True)
    assert "2 étudiant(s) ajouté(s)" in html and "pas admis dans SAS 2026" in html and "INCONNU : introuvable" in html
    assert [r.candidate.full_name for r in j1.candidates] == ["BRAVO", "ALPHA"]  # pasted first, then the class
    assert "08:20" in html and "CHARLIE" in html  # 2nd passage at 08:20; CHARLIE has no jury yet
    html = client.post(f"/soutenances/juries/{j2.id}/candidates", data={"candidates": a.email},
                       follow_redirects=True).get_data(as_text=True)
    assert "déjà dans le jury « Jury 1 »" in html and JuryCandidate.query.count() == 2

    row = JuryCandidate.query.filter_by(candidate_id=a.id).one()
    client.post(f"/soutenances/jury-candidates/{row.id}", data={"move": "-1"})
    db.session.expire_all()
    assert [r.candidate.full_name for r in j1.candidates] == ["ALPHA", "BRAVO"]

    client.post(f"/soutenances/juries/{j1.id}", data={"name": "Jury A", "room": "S2", "duration": "45"})
    db.session.expire_all()
    assert (j1.name, j1.duration, j1.room) == ("Jury A", 45, "S2")
    client.post(f"/soutenances/juries/{j1.id}", data={"delete": "1"})
    assert JuryMember.query.count() == 1 and JuryCandidate.query.count() == 0  # freed
    client.post(f"/soutenances/{d.id}/delete")
    assert Defense.query.count() == Jury.query.count() == JuryMember.query.count() == 0

    client.post("/logout")
    login(client, coach)
    assert client.get("/soutenances/").status_code == 403
