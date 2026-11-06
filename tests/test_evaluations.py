"""Individual SAS evaluation: grid filled by the class's coachs techniques or the admin, never seen by the candidate."""
from app.models import Evaluation, Sas
from app.services import campus_service
from tests.conftest import login, make_candidate, make_user
from tests.test_portal import _logged_candidate

GRID = {"r_technicity": "3", "r_agronomy": "4", "e_agronomy": "Diagnostic parcelle juste, 12/10",
        "r_mutual_aid": "na",
        **{f"r_{k}": "2" for k in ("motivation", "attendance", "respect", "teamwork", "progress")}}


def test_coach_fills_the_grid(app, db, client, admin):
    sas = Sas(name="SAS 2026")
    db.session.add(sas)
    db.session.commit()
    a = make_candidate(email="a@test.ci", cni="A", phone="0101010101", full_name="ALPHA", sas=sas)
    loner = make_candidate(email="l@test.ci", cni="L", phone="0202020202", full_name="LONER", sas=sas)
    coach, other = make_user("coach", "coach@test.ci"), make_user("coach", "coach2@test.ci")
    k = campus_service.create_class(coach, sas.id, "Dev Web")
    a.sas_class = "Dev Web"
    db.session.commit()
    url = f"/campus/students/{a.id}/evaluation"

    login(client, coach)
    assert "Lecture agronomique" in client.get(url).get_data(as_text=True)
    html = client.post(url, data={**GRID, "e_agronomy": ""}, follow_redirects=True).get_data(as_text=True)
    assert "preuve écrite : Lecture agronomique" in html and Evaluation.query.count() == 0  # 4 without evidence
    client.post(url, data={**GRID, "comment": "Bon début"})
    e = Evaluation.query.one()
    # 15 + 20 + 5 x 5 = 60 points over 90 observed (Entraide N/O) -> 67
    assert (e.totals, e.score, e.comment, e.updated_by) == ((60, 90), 67, "Bon début", coach.id)
    assert "67 / 100" in client.get(f"/campus/classes/{k.id}").get_data(as_text=True)
    assert "67" in client.get(f"/campus/students/{a.id}").get_data(as_text=True)
    assert client.get(f"/campus/students/{loner.id}/evaluation").status_code == 404  # in no class

    client.post("/logout")
    login(client, other)  # coach of another class: reads, cannot change
    html = client.get(url).get_data(as_text=True)
    assert "Lecture seule" in html and "<fieldset disabled>" in html and "Enregistrer la fiche" not in html
    assert client.post(url, data=GRID).status_code == 403
    html = client.get("/campus/evaluations").get_data(as_text=True)
    assert "ALPHA" in html and "67" in html and "Voir" in html and "LONER" not in html  # LONER: no class
    assert "ALPHA" not in client.get("/campus/evaluations?state=todo").get_data(as_text=True)
    client.post("/logout")
    login(client, make_user("reception", "rec@test.ci"))  # not a coach
    assert client.get(url).status_code == 403 and client.get("/campus/evaluations").status_code == 403
    assert client.post(url, data=GRID).status_code == 403
    client.post("/logout")
    login(client, admin)
    client.post(url, data={**GRID, "r_mutual_aid": "4", "e_mutual_aid": "Aide ses pairs"})
    db.session.expire_all()
    assert e.score == 70  # 70 / 100

    client.post("/logout")
    c = _logged_candidate(client, email="me@test.ci", cni="ME", phone="0303030303")
    c.sas, c.sas_class = sas, "Dev Web"
    db.session.commit()
    campus_service.save_evaluation(c, coach, campus_service.evaluation_from_form(GRID))
    assert client.get(f"/campus/students/{c.id}/evaluation").status_code in (302, 403)
    assert "valuation" not in client.get("/candidat/").get_data(as_text=True)
