"""Defense sheet: each member rates the sections of his role, shared timer, signatures, deliberation, print."""
import io

from app.models import Defense, DefenseScore, Evaluation, Jury, JuryCandidate, JuryMember, Sas
from app.models.campus import EVAL_CRITERIA
from tests.conftest import login, make_user
from tests.test_portal import _logged_candidate, portal_login

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 64
A = {"r_brief": "4", "r_solution": "4", "r_results": "4", "r_method": "4"}
A_FACTS = {f"e_{k}": "Carte NDVI juste" for k in ("brief", "solution", "results", "method")}
BC = ("structure", "visuals", "speech", "posture", "timing", "answers", "listening")


def test_jury_rates_deliberates_and_prints(app, db, client, admin):
    sas = Sas(name="SAS 2026")
    db.session.add(sas)
    db.session.commit()
    c = _logged_candidate(client, email="a@test.ci", cni="A", phone="0101010101", full_name="ALPHA", sas=sas,
                          sas_class="Web")
    client.post("/logout")
    db.session.add(Evaluation(candidate_id=c.id, ratings={k: "3" for _s, k, *_r in EVAL_CRITERIA}, evidence={}))  # 75
    d = Defense(sas=sas, name="Soutenance finale")
    j = Jury(defense=d, name="Jury 1", duration=20)
    tech, pres, pp, outsider = (make_user("coach", f"{n}@test.ci") for n in ("tech", "pres", "pp", "out"))
    db.session.add_all([d, j])
    db.session.flush()
    db.session.add_all([JuryMember(jury=j, defense_id=d.id, user=u, role=r) for u, r in
                        ((tech, "technical"), (pres, "president"), (pp, "presentation"))])
    row = JuryCandidate(jury=j, defense_id=d.id, candidate=c, position=1)
    db.session.add(row)
    db.session.commit()
    sheet = f"/jury/sheets/{row.id}"

    login(client, tech)
    assert "Jury 1" in client.get("/jury/").get_data(as_text=True)
    client.post(f"/jury/signature/{tech.id}", data={"signature": (io.BytesIO(PNG), "sig.png")},
                content_type="multipart/form-data")
    assert client.get(f"/jury/signature/{tech.id}").data == PNG
    html = client.post(f"{sheet}/score", data=A, follow_redirects=True).get_data(as_text=True)
    assert "fait cité en commentaire" in html and DefenseScore.query.count() == 0  # a 4 needs a fact
    html = client.get(sheet).get_data(as_text=True)
    assert "Compréhension du brief" in html and "Structure et clarté" not in html  # only section A to rate
    client.post(f"{sheet}/score", data={**A, **A_FACTS, "r_structure": "1"})
    assert DefenseScore.query.one().ratings == {k[2:]: "4" for k in A}  # section B ignored for him
    client.post(f"/jury/{j.id}/timer", data={"action": "start", "minutes": "20"})
    client.post(f"/jury/{j.id}/timer", data={"action": "pause"})
    assert "En pause" in client.get(f"/jury/{j.id}/timer").get_data(as_text=True)
    db.session.expire_all()
    assert 1190 <= j.timer_left <= 1200

    client.post("/logout")
    login(client, pres)
    client.post(f"{sheet}/score", data={f"r_{k}": "3" for k in (*BC, "brief")})  # A not his
    client.post(f"{sheet}/info", data={"part": "header", "subject": "Drone et NDVI", "format": "individual",
                                       "supports": ["slides", "hack"], "talk_minutes": "10"})
    client.post(f"{sheet}/info", data={"part": "appraisal", "decision": "admitted", "strengths": "Clair",
                                       "positives": ["plan"]})
    client.post("/logout")
    login(client, pp)
    assert "une fois votre propre notation" in client.get(sheet).get_data(as_text=True)  # rates alone first
    client.post(f"{sheet}/score", data={**{f"r_{k}": "2" for k in BC}, "r_listening": "na"})
    # A 45/45 · B (26.25 + 17.5) / 2 · C (15 + 7.5) / 2 over (20 + 15) / 2 -> 78.125 / 97.5 -> 80; final (80 + 75) / 2
    html = client.get(sheet).get_data(as_text=True)
    assert "77.5" in html and "Plus de 15 points" in html  # B+C: 41.25 vs 25
    db.session.expire_all()
    assert row.sheet["subject"] == "Drone et NDVI" and row.sheet["supports"] == ["slides"]

    client.post("/logout")
    login(client, outsider)
    assert client.get(sheet).status_code == 403 and client.get(f"/jury/signature/{tech.id}").status_code == 403
    client.post("/logout")
    portal_login(client, c.email)
    assert "Résultat de soutenance" not in client.get("/candidat/").get_data(as_text=True)  # not deliberated yet
    assert client.get(sheet).status_code == 403

    client.post("/logout")
    login(client, admin)
    assert client.get(f"{sheet}/print").status_code == 302  # validate first
    client.post(f"/jury/signature/{pres.id}", data={"signature": (io.BytesIO(PNG), "p.png"), "defense_id": d.id},
                content_type="multipart/form-data")
    client.post(f"/soutenances/juries/{j.id}/validate")
    html = client.get(f"{sheet}/print").get_data(as_text=True)
    assert "77.5" in html and "Drone et NDVI" in html and f"/jury/signature/{pres.id}" in html
    assert "☑ Diaporama" in html and "☑ Admis" in html
    client.post("/logout")
    login(client, tech)
    html = client.post(f"{sheet}/score", data={**A, **A_FACTS, "r_brief": "1"}, follow_redirects=True).get_data(
        as_text=True)
    assert "Délibération validée" in html  # locked
    client.post("/logout")
    portal_login(client, c.email)
    html = client.get("/candidat/").get_data(as_text=True)
    assert "Résultat de soutenance" in html and "77.5" in html and "Admis" in html


def test_jury_only_profiles(app, db, client, admin):
    for role in ("partner", "cme"):
        user = make_user(role, f"{role}@test.ci")
        login(client, user)
        assert client.get("/").headers["Location"].endswith("/jury/")
        html = client.get("/jury/").get_data(as_text=True)
        assert "Mes jurys" in html and "/candidates/" not in html  # no recruitment menu
        assert client.get("/candidates/").status_code == 403 and client.get("/campus/").status_code == 403
        client.post("/logout")
