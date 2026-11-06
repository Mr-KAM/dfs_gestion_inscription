"""Defense sheet: each member rates the whole grid, shared timer, signatures, deliberation, print."""
import io

from app.models import Defense, DefenseScore, Evaluation, Jury, JuryCandidate, JuryMember, Sas
from app.models.campus import EVAL_CRITERIA
from tests.conftest import login, make_candidate, make_user
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
    tech, pres, outsider = (make_user("coach", f"{n}@test.ci") for n in ("tech", "pres", "out"))
    pp = make_user("partner", "pp@test.ci")  # any profile rates, not just coaches
    db.session.add_all([d, j])
    db.session.flush()
    db.session.add_all([JuryMember(jury=j, defense_id=d.id, user=u, role=r) for u, r in
                        ((tech, "technical"), (pres, "president"), (pp, "presentation"))])
    bravo = make_candidate(email="b@test.ci", cni="B", phone="0202020202", full_name="BRAVO", sas=sas, sas_class="Web")
    row = JuryCandidate(jury=j, defense_id=d.id, candidate=c, position=1)
    bravo_row = JuryCandidate(jury=j, defense_id=d.id, candidate=bravo, position=0)  # passes first
    db.session.add_all([row, bravo_row])
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
    assert "Compréhension du brief" in html and "Structure et clarté" in html  # the whole grid, whatever the role
    client.post(f"{sheet}/score", data={**A, **A_FACTS, **{f"r_{k}": "3" for k in BC}})
    assert DefenseScore.query.one().ratings == {**{k[2:]: "4" for k in A}, **{k: "3" for k in BC}}
    client.post(f"/jury/{j.id}/timer", data={"action": "start", "minutes": "20"})
    client.post(f"/jury/{j.id}/timer", data={"action": "pause"})
    assert "En pause" in client.get(f"/jury/{j.id}/timer").get_data(as_text=True)
    db.session.expire_all()
    assert 1190 <= j.timer_left <= 1200

    client.post("/logout")
    login(client, pres)
    client.post(f"{sheet}/score", data={f"r_{k}": "3" for k in (*BC, *(a[2:] for a in A))})
    client.post(f"{sheet}/info", data={"part": "header", "subject": "Drone et NDVI", "format": "individual",
                                       "supports": ["slides", "hack"], "talk_minutes": "10"})
    client.post(f"{sheet}/info", data={"part": "appraisal", "decision": "admitted", "strengths": "Clair",
                                       "positives": ["plan"]})
    client.post("/logout")
    login(client, pp)
    assert "une fois votre propre notation" in client.get(sheet).get_data(as_text=True)  # rates alone first
    client.post(f"{sheet}/score", data={**{f"r_{k}": "2" for k in (*BC, *(a[2:] for a in A))}, "r_listening": "na"})
    # A (45 + 33.75 + 22.5) / 3 · B (26.25 + 26.25 + 17.5) / 3 · C (15 + 15 + 7.5) / 3 over (20 + 20 + 15) / 3
    # -> 69.58 / 98.33 -> 71; final (2 x 75 + 71) / 3
    html = client.get(sheet).get_data(as_text=True)
    assert "73.7 / 100" in html and "Plus de 15 points" in html  # 86.25 / 100 vs 50 / 100
    assert "÷ 98.3 poids applicables" in html
    assert '<fieldset class="col-12" disabled' in html  # the decision is not theirs
    db.session.expire_all()
    assert row.sheet["subject"] == "Drone et NDVI" and row.sheet["supports"] == ["slides"]
    assert row.sheet["strengths"] == "Clair" and "decision" not in row.sheet  # the posted decision is ignored

    client.post("/logout")
    login(client, outsider)
    assert client.get(sheet).status_code == 403 and client.get(f"/jury/signature/{tech.id}").status_code == 403
    client.post("/logout")
    login(client, pres)
    global_jury = f"/soutenances/{d.id}/jury-global"
    decide = {f"decision_{row.id}": "admitted", f"note_{row.id}": "Stage terrain"}
    assert client.get(global_jury).status_code == 403 and client.post(global_jury, data=decide).status_code == 403
    client.post("/logout")
    portal_login(client, c.email)
    assert "Résultat de soutenance" not in client.get("/candidat/").get_data(as_text=True)  # not deliberated yet
    assert client.get(sheet).status_code == 403

    client.post("/logout")
    login(client, admin)
    assert client.get(f"{sheet}/print").status_code == 302  # validate first
    client.post(f"/jury/signature/{pres.id}", data={"signature": (io.BytesIO(PNG), "p.png"), "defense_id": d.id},
                content_type="multipart/form-data")
    html = client.post(global_jury, data=decide, follow_redirects=True).get_data(as_text=True)
    assert "Délibération à valider" in html  # the juries' notes can still change
    db.session.expire_all()
    assert "decision" not in row.sheet
    client.post(f"/soutenances/juries/{j.id}/validate")
    html = client.post(global_jury, data=decide, follow_redirects=True).get_data(as_text=True)
    assert "73.7" in html and html.index("ALPHA") < html.index("BRAVO")  # by grade, not by passage order
    html = client.get(f"{sheet}/print").get_data(as_text=True)
    assert "73.7 / 100" in html and "Drone et NDVI" in html and f"/jury/signature/{pres.id}" in html
    assert "☑ Diaporama" in html and "☑ Admis" in html
    assert "Procès-verbal de jury de soutenance" in html and "data-fit-page" in html
    assert "Fiche globale" not in html and "Commentaire" not in html and "Candidat" not in html
    assert "Carte NDVI juste" not in html  # the cited facts stay on the screen sheet
    client.post("/logout")
    login(client, tech)
    html = client.post(f"{sheet}/score", data={**A, **A_FACTS, "r_brief": "1"}, follow_redirects=True).get_data(
        as_text=True)
    assert "Délibération validée" in html  # locked
    client.post("/logout")
    portal_login(client, c.email)
    assert "Résultat de soutenance" not in client.get("/candidat/").get_data(as_text=True)  # validated, not sent
    client.post("/logout")
    login(client, admin)
    client.post(global_jury, data={f"decision_{bravo_row.id}": "rejected"})
    client.post(f"/soutenances/{d.id}/resultats")
    client.post("/logout")
    portal_login(client, c.email)
    html = client.get("/candidat/").get_data(as_text=True)
    assert "Résultat de soutenance" in html and "73.7 / 100" in html and "Admis : Stage terrain" in html


def test_jury_only_profiles(app, db, client, admin):
    for role in ("partner", "cme"):
        user = make_user(role, f"{role}@test.ci")
        login(client, user)
        assert client.get("/").headers["Location"].endswith("/jury/")
        html = client.get("/jury/").get_data(as_text=True)
        assert "Mes jurys" in html and "/candidates/" not in html  # no recruitment menu
        assert client.get("/candidates/").status_code == 403 and client.get("/campus/").status_code == 403
        client.post("/logout")
