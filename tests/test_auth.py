from app.cli import ensure_admin
from app.models import AuditLog, User
from app.models.user import ROLE_MOTIVATION
from tests.conftest import login, make_user


def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.get_json() == {"status": "ok", "database": "connected"}


def test_login_valid_records_last_login_and_audit(client, admin, db):
    response = login(client, admin)
    assert response.status_code == 302 and response.headers["Location"] == "/"
    assert db.session.get(User, admin.id).last_login_at is not None
    assert db.session.query(AuditLog).filter_by(action="login").count() == 1
    assert client.get("/").status_code == 200


def test_login_invalid_password(client, admin):
    response = client.post("/login", data={"email": admin.email, "password": "wrong"})
    assert response.status_code == 200
    assert "incorrect" in response.get_data(as_text=True)
    assert client.get("/").status_code == 302  # still anonymous


def test_inactive_user_cannot_login(client, app):
    user = make_user(ROLE_MOTIVATION, active=False)
    login(client, user)
    assert client.get("/").status_code == 302


def test_anonymous_redirected_to_login(client):
    response = client.get("/candidates/")
    assert response.status_code == 302 and "/login" in response.headers["Location"]


def test_open_redirect_blocked(client, admin):
    response = client.post("/login?next=https://evil.example", data={"email": admin.email, "password": "Password123!"})
    assert response.headers["Location"] == "/"


def test_role_permissions_enforced_server_side(client, motivation_user, technical_user):
    login(client, technical_user)
    assert client.get("/admin/users").status_code == 403
    assert client.get("/admin/kobo/").status_code == 403
    assert client.get("/motivation/").status_code == 403
    assert client.post("/motivation/1/validate", data={"piece_number": "x"}).status_code == 403
    assert client.get("/technical/rooms").status_code == 200
    client.post("/logout")

    login(client, motivation_user)
    assert client.get("/admin/users").status_code == 403
    assert client.get("/technical/rooms").status_code == 403
    assert client.post("/technical/start/1").status_code == 403
    assert client.get("/motivation/").status_code == 200


def test_admin_can_create_user(client, admin, db):
    login(client, admin)
    response = client.post("/admin/users/new", data={
        "email": "New@Test.ci", "full_name": "Nouveau", "role": "technical_tester", "password": "Secret123!"})
    assert response.status_code == 302
    user = db.session.query(User).filter_by(email="new@test.ci").one()
    assert user.role == "technical_tester" and user.check_password("Secret123!")
    assert user.password_hash != "Secret123!"


def test_ensure_admin_is_idempotent(app, db):
    assert "créé" in ensure_admin()
    assert "existe déjà" in ensure_admin()
    assert db.session.query(User).count() == 1


def test_csrf_enforced_when_enabled(app, admin):
    app.config["WTF_CSRF_ENABLED"] = True
    response = app.test_client().post("/login", data={"email": admin.email, "password": "Password123!"})
    assert response.status_code == 400
