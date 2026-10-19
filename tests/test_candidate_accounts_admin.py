"""Admin > Utilisateurs > Candidats: follow candidate profiles and manage their logins."""
from app.models import Candidate, User
from app.services import account_service
from tests.conftest import PASSWORD, login, make_candidate

BACK = "/admin/users?tab=candidats"


def test_candidates_tab_lists_and_searches(app, client, admin):
    make_candidate(email="aya@test.ci")
    make_candidate(email="bob@test.ci", cni="B", full_name="BAMBA Bob")
    account_service.ensure_candidate_accounts()
    login(client, admin)
    html = client.get(BACK).get_data(as_text=True)
    assert "KOUAME Aya" in html and "BAMBA Bob" in html and "Initial, à changer" in html
    html = client.get(BACK + "&q=bamba").get_data(as_text=True)
    assert "BAMBA Bob" in html and "KOUAME Aya" not in html
    assert "aya@test.ci" not in client.get("/admin/users").get_data(as_text=True)  # staff tab unchanged


def test_reset_toggle_delete_from_tab(app, db, client, admin):
    c = make_candidate()
    account_service.ensure_candidate_accounts()
    acc = c.account
    acc.set_password("Perso1234")
    acc.must_change_password = False
    db.session.commit()
    login(client, admin)

    resp = client.post(f"/candidates/{c.id}/reset-password", data={"next": BACK})
    assert resp.headers["Location"].endswith(BACK) and db.session.get(User, acc.id).must_change_password

    client.post(f"/admin/users/{acc.id}/toggle", data={"next": BACK})
    assert not db.session.get(User, acc.id).active
    other = client.application.test_client()
    other.post("/candidat/connexion", data={"email": acc.email, "password": PASSWORD})
    assert other.get("/candidat/").status_code != 200  # deactivated: no access to the candidate space

    resp = client.post(f"/candidates/{c.id}/delete", data={"next": BACK + "&page=9"})
    assert resp.headers["Location"].endswith(BACK + "&page=9")
    assert db.session.get(Candidate, c.id) is None and db.session.get(User, acc.id) is None
    assert client.get(BACK + "&page=9").status_code == 200  # page past the end: empty, not 404
