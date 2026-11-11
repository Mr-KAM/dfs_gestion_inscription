"""Training cohorts: admitted SAS students made eligible, registered at the desk, DFS matricule, Espace Zeus."""
import base64

from app.models import Cohort, Defense, Enrollment, Jury, JuryCandidate, Sas
from app.services import account_service
from app.services.cohort_service import matricule_prefix
from tests.conftest import login, make_candidate, make_user
from tests.test_portal import portal_login


def _png_data_url() -> str:
    return "data:image/png;base64," + base64.b64encode(bytes.fromhex("89504e470d0a1a0a") + bytes(32)).decode()


def test_cohort_registration_flow(app, db, client, admin):
    sas = Sas(name="SAS 2026")
    db.session.add(sas)
    db.session.commit()
    people = {name: make_candidate(full_name=name, email=f"{name}@test.ci", cni=name, phone=f"07000000{i:02d}", sas=sas)
              for i, name in enumerate(("ALPHA", "BRAVO", "CHARLIE"))}
    defense = Defense(sas=sas, name="Soutenance")
    jury = Jury(defense=defense, name="Jury 1")
    db.session.add_all([defense, jury])
    db.session.flush()
    for name, decision in (("ALPHA", "admitted"), ("BRAVO", "reserved"), ("CHARLIE", "rejected")):
        db.session.add(JuryCandidate(jury=jury, defense_id=defense.id, candidate_id=people[name].id,
                                     sheet={"decision": decision}))
    db.session.commit()

    login(client, admin)
    client.post("/cohortes/", data={"name": "2026-2027"})
    cohort = Cohort.query.one()
    html = client.get(f"/cohortes/{cohort.id}?sas={sas.id}").get_data(as_text=True)
    assert "ALPHA" in html and "BRAVO" in html and "CHARLIE" not in html  # only the admitted
    # A forged id (CHARLIE, rejected) is ignored.
    client.post(f"/cohortes/{cohort.id}/eligibles",
                data={"sas_id": sas.id, "candidate_ids": [people[n].id for n in ("ALPHA", "BRAVO", "CHARLIE")]})
    assert {e.candidate.full_name for e in Enrollment.query} == {"ALPHA", "BRAVO"}
    client.post("/logout")

    reception = make_user("reception")
    login(client, reception)
    alpha = Enrollment.query.filter_by(candidate_id=people["ALPHA"].id).one()
    html = client.post(f"/cohortes/inscription/{alpha.id}", data={"action": "validate", "arrival_date": "2026-11-15"},
                       follow_redirects=True).get_data(as_text=True)
    assert "et signature de l&#39;étudiant requises" in html and alpha.matricule is None
    client.post(f"/cohortes/inscription/{alpha.id}", data={
        "action": "validate", "arrival_date": "2026-11-15", "signature": _png_data_url(), "bedroom": "A12"})
    bravo = Enrollment.query.filter_by(candidate_id=people["BRAVO"].id).one()
    client.post(f"/cohortes/inscription/{bravo.id}", data={
        "action": "validate", "arrival_date": "2026-11-16", "signature": _png_data_url()})
    db.session.expire_all()
    prefix = matricule_prefix()
    assert (alpha.matricule, bravo.matricule) == (f"{prefix}000001", f"{prefix}000002")
    assert prefix.endswith("DFS") and alpha.bedroom == "A12"
    assert client.get(f"/cohortes/inscription/{alpha.id}/signature").mimetype == "image/png"
    # Residence assigns the equipment later; the matricule never changes.
    client.post("/logout")
    login(client, make_user("residence"))
    client.post(f"/cohortes/inscription/{alpha.id}", data={
        "action": "validate", "arrival_date": "2026-11-15", "bedroom": "B3", "equipment": "PC-07"})
    db.session.expire_all()
    assert (alpha.matricule, alpha.bedroom, alpha.equipment) == (f"{prefix}000001", "B3", "PC-07")
    assert client.post(f"/cohortes/{cohort.id}/eligibles", data={"sas_id": sas.id}).status_code == 403
    client.post("/logout")

    login(client, make_user("coach"))
    assert client.get("/cohortes/").status_code == 403
    client.post("/logout")

    # Espace Zeus: the registered student sees his DFS matricule.
    account = account_service.sync_account(people["ALPHA"]) or people["ALPHA"].account
    account.must_change_password = False
    db.session.commit()
    portal_login(client, "ALPHA@test.ci")
    html = client.get("/candidat/zeus").get_data(as_text=True)
    assert f"{prefix}000001" in html and "B3" in html and "PC-07" in html
