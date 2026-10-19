"""SAS (next recruitment stage): admin creates a SAS, admits candidates, assigns bedroom + equipment; candidates see it."""
from app.models import Sas
from tests.conftest import login
from tests.test_portal import _logged_candidate, make_candidate


def _sas(client, name="SAS 2026"):
    client.post("/admin/sas/save", data={"name": name, "description": "Campus DFS"})
    return Sas.query.filter_by(name=name).one()


def test_admin_creates_sas_admits_and_assigns(app, db, client, admin):
    a = make_candidate(email="a@test.ci", motivation_passed=True, technical_passed=True,
                       motivation_completed=True, technical_completed=True, exam_code="E1")
    b = make_candidate(email="b@test.ci", full_name="B")
    make_candidate(email="c@test.ci", full_name="C")  # not admitted
    login(client, admin)
    sas = _sas(client)
    assert "1 candidat(s) ayant validé" in client.get(f"/admin/sas/{sas.id}").get_data(as_text=True)

    client.post(f"/admin/sas/{sas.id}/members", data={"validated": "1"})  # both tests validated -> a
    resp = client.post(f"/admin/sas/{sas.id}/members", data={"candidates": f"{b.matricule.lower()}, a@test.ci\nINCONNU"},
                       follow_redirects=True)
    html = resp.get_data(as_text=True)
    assert "1 candidat(s) admis" in html and "INCONNU" in html  # a already in: not counted twice
    db.session.expire_all()
    assert {c.email for c in sas.members} == {"a@test.ci", "b@test.ci"}

    client.post(f"/admin/sas/{sas.id}/members/{b.id}", data={"bedroom": "B-12", "equipment": "Laptop n°12"})
    db.session.expire_all()
    assert (b.sas_bedroom, b.sas_equipment) == ("B-12", "Laptop n°12")

    assert client.post(f"/admin/sas/{sas.id}/delete").status_code == 302 and db.session.get(Sas, sas.id)  # not empty
    client.post(f"/admin/sas/{sas.id}/members/{b.id}", data={"remove": "1"})
    db.session.expire_all()
    assert b.sas_id is None and b.sas_bedroom == ""
    assert client.post(f"/admin/sas/{sas.id}/members/{b.id}", data={}).status_code == 404  # no longer a member


def test_candidate_sees_admission(app, db, client):
    c = _logged_candidate(client)
    assert "admis(e) au SAS" not in client.get("/candidat/").get_data(as_text=True)
    c.sas, c.sas_bedroom = Sas(name="SAS 2026"), "B-12"
    db.session.commit()
    html = client.get("/candidat/").get_data(as_text=True)
    assert "admis(e) au SAS" in html and "B-12" in html and "attribution" in html  # equipment not yet given


def test_sas_is_admin_only(app, client, motivation_user):
    login(client, motivation_user)
    assert client.get("/admin/sas").status_code == 403
    assert client.post("/admin/sas/save", data={"name": "X"}).status_code == 403
