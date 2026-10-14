"""Candidate accounts: default logins for loaded candidates, sign-up, own space only."""
import io
from datetime import date

import pytest
from sqlalchemy.exc import IntegrityError

from app.models import AuditLog, Candidate, User
from app.services import account_service
from tests.conftest import login, make_candidate

DEFAULT = "00000000"


def portal_login(client, email, password=DEFAULT):
    return client.post("/candidat/connexion", data={"email": email, "password": password}, follow_redirects=True)


def test_every_loaded_candidate_gets_a_default_login(app, db):
    a = make_candidate(email="Aya@Test.ci", cni="A")
    make_candidate(email="", cni="B", full_name="Sans Email")
    make_candidate(email="aya@test.ci", cni="C", full_name="Doublon Email")  # same email as Aya
    result = account_service.ensure_candidate_accounts()
    assert result["created"] == 1
    assert [c.full_name for c in result["no_email"]] == ["Sans Email"]
    assert [c.full_name for c in result["conflicts"]] == ["Doublon Email"]
    account = db.session.get(Candidate, a.id).account
    assert account.email == "aya@test.ci" and account.role == "candidate"
    assert account.check_password(DEFAULT) and account.must_change_password
    assert account_service.ensure_candidate_accounts()["created"] == 0  # idempotent


def test_cli_creates_accounts(app, db):
    make_candidate()
    out = app.test_cli_runner().invoke(args=["create-candidate-accounts"]).output
    assert "Comptes candidats créés : 1" in out and db.session.query(User).count() == 1


def test_first_login_forces_password_change(client, app):
    make_candidate(email="aya@test.ci")
    account_service.ensure_candidate_accounts()
    page = portal_login(client, "AYA@test.ci").get_data(as_text=True)  # email is case-insensitive
    assert "Choisissez votre mot de passe" in page
    assert client.get("/candidat/", follow_redirects=True).request.path == "/candidat/mot-de-passe"
    bad = client.post("/candidat/mot-de-passe", data={"current_password": DEFAULT, "password": DEFAULT,
                                                      "password_confirm": DEFAULT}, follow_redirects=True)
    assert "différent du mot de passe initial" in bad.get_data(as_text=True)
    ok = client.post("/candidat/mot-de-passe", data={"current_password": DEFAULT, "password": "MonSecret1",
                                                     "password_confirm": "MonSecret1"}, follow_redirects=True)
    assert "Bonjour, KOUAME Aya" in ok.get_data(as_text=True)
    client.post("/logout")
    assert "Bonjour" not in portal_login(client, "aya@test.ci").get_data(as_text=True)  # old password dead
    assert "Bonjour" in portal_login(client, "aya@test.ci", "MonSecret1").get_data(as_text=True)


def _logged_candidate(client, **kwargs):
    c = make_candidate(**kwargs)
    account = account_service.sync_account(c) or c.account
    account.must_change_password = False
    from app.extensions import db
    db.session.commit()
    portal_login(client, c.email)
    return c


def test_candidate_sees_only_own_data(client, app):
    other = make_candidate(email="other@test.ci", cni="Z", full_name="AUTRE Personne", exam_code="SECRET-9",
                           motivation_completed=True, piece_number="P9")
    _logged_candidate(client, motivation_completed=True, exam_code="dfs-Ab1", piece_number="P-1",
                      group_name="G2", scheduled_date=date(2026, 10, 20), scheduled_time="10:00")
    page = client.get("/candidat/").get_data(as_text=True)
    for expected in ("dfs-Ab1", "P-1", "G2", "20/10/2026", "10:00", "js-copy"):
        assert expected in page, expected
    # No candidate id in any portal URL: nothing to tamper with to reach someone else's file.
    assert other.full_name not in page and "SECRET-9" not in page


def test_candidate_has_no_staff_access(client, app):
    c = _logged_candidate(client)
    for url in ("/candidates/", f"/candidates/{c.id}", "/motivation/", "/technical/rooms", "/technical/sessions",
                "/admin/users", "/admin/kobo/", "/admin/imports", "/admin/exports", "/admin/settings"):
        assert client.get(url).status_code == 403, url
    assert client.post(f"/motivation/{c.id}/validate", data={"piece_number": "x"}).status_code == 403
    assert client.get("/").headers["Location"] == "/candidat/"
    assert "Utilisateurs" not in client.get("/candidat/").get_data(as_text=True)


def test_staff_cannot_open_candidate_space(client, admin):
    login(client, admin)
    assert client.get("/candidat/").status_code == 403


def test_signup_creates_candidate_and_logs_in(client, db):
    response = client.post("/candidat/inscription", data={
        "full_name": "  TRAORE   Fatou ", "email": "Fatou@Mail.ci", "phone": "+225 07 07 07 07 99",
        "cni": "ci999", "birth_date": "2001-05-04", "gender": "F", "city": "Abidjan",
        "password": "Secret123", "password_confirm": "Secret123"}, follow_redirects=True)
    assert "Bonjour, TRAORE Fatou" in response.get_data(as_text=True)  # no forced change: own password
    c = db.session.query(Candidate).one()
    assert (c.email, c.phone, c.cni) == ("fatou@mail.ci", "0707070799", "CI999")
    assert c.account.check_password("Secret123") and not c.account.must_change_password
    assert db.session.query(AuditLog).filter_by(action="candidate_signup").count() == 1


@pytest.mark.parametrize("field,value,message", [
    ("email", "aya@test.ci", "Un compte existe déjà"),
    ("phone", "0707070707", "Un dossier existe déjà"),
    ("cni", "CI001", "Un dossier existe déjà"),
])
def test_signup_never_duplicates_a_loaded_candidate(client, db, field, value, message):
    make_candidate(email="aya@test.ci", phone="0707070707", cni="CI001")
    account_service.ensure_candidate_accounts()
    form = {"full_name": "X", "email": "new@test.ci", "phone": "0101010101", "cni": "",
            "password": "Secret123", "password_confirm": "Secret123", field: value}
    page = client.post("/candidat/inscription", data=form).get_data(as_text=True)
    assert message in page and db.session.query(Candidate).count() == 1


def test_signup_password_rules(client, db):
    form = {"full_name": "X", "email": "x@test.ci", "phone": "0101010101", "password": "Secret123",
            "password_confirm": "Autre1234"}
    assert "ne correspondent pas" in client.post("/candidat/inscription", data=form).get_data(as_text=True)
    form["password"] = form["password_confirm"] = "court"
    assert "au moins 8" in client.post("/candidat/inscription", data=form).get_data(as_text=True)
    assert db.session.query(Candidate).count() == 0


def test_import_creates_logins(client, admin, db):
    header = "Nom et prénoms;Email;Téléphone\n"
    login(client, admin)
    client.post("/admin/imports", data={"file": (io.BytesIO((header + "KONE Awa;awa@t.ci;0707000001\n").encode()),
                                                 "c.csv")}, content_type="multipart/form-data")
    page = client.post("/admin/imports/confirm", data={"map_full_name": "Nom et prénoms", "map_email": "Email",
                                                      "map_phone": "Téléphone"}).get_data(as_text=True)
    assert "1 compte(s) candidat créé(s)" in page
    assert db.session.query(User).filter_by(email="awa@t.ci").one().check_password(DEFAULT)


def test_admin_email_change_follows_login_and_reset(client, admin, db):
    c = make_candidate()
    account_service.ensure_candidate_accounts()
    login(client, admin)
    client.post(f"/candidates/{c.id}/edit", data={"full_name": c.full_name, "email": "New@Test.ci", "phone": c.phone})
    assert db.session.get(Candidate, c.id).account.email == "new@test.ci"
    c.account.set_password("Perso1234")
    c.account.must_change_password = False
    db.session.commit()
    client.post(f"/candidates/{c.id}/reset-password")
    account = db.session.get(Candidate, c.id).account
    assert account.check_password(DEFAULT) and account.must_change_password
    assert "new@test.ci" not in client.get("/admin/users").get_data(as_text=True)  # staff list only
    assert client.get(f"/admin/users/{account.id}/edit").status_code == 404


def test_deleting_candidate_deletes_login(client, admin, db):
    c = make_candidate()
    account_service.ensure_candidate_accounts()
    login(client, admin)
    client.post(f"/candidates/{c.id}/delete")
    assert db.session.query(User).filter_by(role="candidate").count() == 0


def test_database_ties_candidate_role_to_a_candidate(app, db):
    user = User(email="x@t.ci", role="candidate", password_hash="x")  # no candidate_id
    db.session.add(user)
    with pytest.raises(IntegrityError):
        db.session.commit()
    db.session.rollback()
