"""'Mot de passe oublié' from the login pages: request recorded for the admins, who reset it (no email sending)."""
from app.models import PasswordResetRequest, User
from app.services import account_service
from tests.conftest import PASSWORD, login, make_user
from tests.test_portal import DEFAULT, _logged_candidate, make_candidate

SENT = "Demande enregistrée"


def _ask(client, email, message="", espace=None):
    url = "/mot-de-passe-oublie" + ("?espace=candidat" if espace else "")
    return client.post(url, data={"email": email, "message": message}, follow_redirects=True)


def test_request_from_both_login_pages(app, db, client):
    assert "/mot-de-passe-oublie" in client.get("/login").get_data(as_text=True)
    assert "/mot-de-passe-oublie?espace=candidat" in client.get("/candidat/connexion").get_data(as_text=True)
    c = make_candidate()
    account_service.ensure_candidate_accounts()
    resp = _ask(client, " AYA@test.ci ", "07 07 07 07 07", espace=True)
    assert SENT in resp.get_data(as_text=True) and resp.request.path == "/candidat/connexion"
    assert SENT in _ask(client, "inconnu@test.ci").get_data(as_text=True)  # same answer: no account enumeration
    _ask(client, "aya@test.ci", "rappel")
    requests = PasswordResetRequest.query.all()
    assert [(r.user.candidate_id, r.message, r.status) for r in requests] == [(c.id, "rappel", "pending")]  # one only
    assert SENT in _ask(client, "aya@test.ci").get_data(as_text=True)  # 3rd request of the hour: still accepted
    assert "Trop de demandes" in _ask(client, "aya@test.ci").get_data(as_text=True)  # 3 per email and per hour
    assert "Adresse email invalide" in _ask(client, "pas-un-email").get_data(as_text=True)


def test_admin_resets_candidate_and_staff(app, db, client, admin):
    make_candidate()
    account_service.ensure_candidate_accounts()
    coach = make_user("coach", "coach@test.ci")
    _ask(client, "aya@test.ci")
    _ask(client, "coach@test.ci", "bureau 2")
    login(client, admin)
    page = client.get("/admin/users").get_data(as_text=True)
    assert "Demandes de réinitialisation de mot de passe (2)" in page and "bureau 2" in page and "text-bg-warning" in page
    cand_req, coach_req = sorted(PasswordResetRequest.query.all(), key=lambda r: r.user.email)
    cand_account = cand_req.user
    cand_account.set_password("Personnel123")
    db.session.commit()

    html = client.post(f"/admin/reset-requests/{cand_req.id}", data={"approve": "1"},
                       follow_redirects=True).get_data(as_text=True)
    assert "remis au mot de passe initial" in html
    db.session.expire_all()
    assert cand_account.check_password(DEFAULT) and cand_account.must_change_password and cand_req.status == "done"

    html = client.post(f"/admin/reset-requests/{coach_req.id}", data={"approve": "1"}).get_data(as_text=True)
    db.session.expire_all()
    temporary = html.split('data-copy="')[1].split('"')[0]
    assert db.session.get(User, coach.id).check_password(temporary) and db.session.get(User, coach.id).must_change_password
    assert "déjà été traitée" in client.post(f"/admin/reset-requests/{coach_req.id}", data={"approve": "1"},
                                             follow_redirects=True).get_data(as_text=True)
    assert "Demandes de réinitialisation" not in client.get("/admin/users").get_data(as_text=True)


def test_reject_keeps_password(app, db, client, admin):
    coach = make_user("coach", "coach@test.ci")
    _ask(client, "coach@test.ci")
    login(client, admin)
    client.post(f"/admin/reset-requests/{PasswordResetRequest.query.one().id}", data={"approve": "0"})
    db.session.expire_all()
    assert PasswordResetRequest.query.one().status == "rejected" and db.session.get(User, coach.id).check_password(PASSWORD)


def test_staff_with_temporary_password_must_change_it(app, db, client, admin):
    coach = make_user("coach", "coach@test.ci")
    account_service.request_reset("coach@test.ci", "", "1.2.3.4")
    temporary = account_service.handle_reset(PasswordResetRequest.query.one(), admin, True)
    client.post("/login", data={"email": "coach@test.ci", "password": temporary})
    resp = client.get("/campus/")
    assert resp.status_code == 302 and resp.headers["Location"].endswith("/profil")
    client.post("/profil", data={"action": "password", "current_password": temporary,
                                 "password": "Nouveau123", "password_confirm": "Nouveau123"})
    assert client.get("/campus/").status_code == 200
    db.session.expire_all()
    assert not db.session.get(User, coach.id).must_change_password


def test_reset_requests_admin_only(app, db, client):
    _logged_candidate(client)
    assert client.post("/admin/reset-requests/1", data={"approve": "1"}).status_code == 403
