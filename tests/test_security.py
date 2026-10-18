"""Security fixes from the audit: sessions, brute force, exports, headers, deployment."""
import io
import os
import pathlib
import re
import time

import pandas as pd

import app.auth as auth
import app.portal as portal
from app.models import AuditLog
from app.models.user import ROLE_MOTIVATION, load_user
from app.services import account_service
from tests.conftest import PASSWORD, login, make_candidate, make_user

ROOT = pathlib.Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------- sessions

def test_deactivated_user_is_logged_out_immediately(client, app, db, admin):
    tester = make_user(ROLE_MOTIVATION)
    login(client, tester)
    assert client.get("/candidates/").status_code == 200
    tester.active = False  # what admin.user_toggle does
    db.session.commit()
    assert client.get("/candidates/").status_code == 302  # session no longer valid


def test_password_change_logs_out_other_sessions(client, db):
    """Session ids embed a password fingerprint: once it changes, every older session id is refused.
    (Checked on load_user directly: test clients sharing one app context share Flask-Login's cache.)"""
    c = make_candidate()
    account_service.ensure_candidate_accounts()
    c.account.must_change_password = False
    db.session.commit()
    old_session_id = c.account.get_id()
    client.post("/candidat/connexion", data={"email": c.email, "password": "00000000"})
    client.post("/candidat/mot-de-passe", data={"current_password": "00000000", "password": "MonSecret1",
                                                "password_confirm": "MonSecret1"})
    assert client.get("/candidat/").status_code == 200  # the device that changed it stays logged in
    assert load_user(old_session_id) is None  # any other device still holding the old id is out
    assert load_user(c.account.get_id()) is c.account
    assert load_user(f"{c.account.id}") is None and load_user("garbage") is None


def test_admin_reset_ends_candidate_sessions(client, db, admin):
    c = make_candidate()
    account_service.ensure_candidate_accounts()
    c.account.set_password("Perso1234")
    db.session.commit()
    candidate_session_id = c.account.get_id()
    login(client, admin)
    client.post(f"/candidates/{c.id}/reset-password")
    assert load_user(candidate_session_id) is None


# ---------------------------------------------------------------- brute force

def test_login_blocked_after_repeated_failures_on_one_email(client, admin):
    for _ in range(auth.MAX_FAILS_PER_EMAIL):
        client.post("/login", data={"email": admin.email, "password": "wrong"})
    page = client.post("/login", data={"email": admin.email, "password": PASSWORD}).get_data(as_text=True)
    assert "Trop de tentatives" in page  # even the right password is refused during the window
    assert client.get("/").status_code == 302
    other = make_user(ROLE_MOTIVATION)
    assert login(client, other).status_code == 302  # other accounts unaffected


def test_login_blocked_per_ip(client, app, monkeypatch):
    monkeypatch.setattr(auth, "MAX_FAILS_PER_IP", 3)
    for i in range(3):
        client.post("/candidat/connexion", data={"email": f"x{i}@t.ci", "password": "00000000"})
    page = client.post("/candidat/connexion", data={"email": "y@t.ci", "password": "x"}).get_data(as_text=True)
    assert "Trop de tentatives" in page


def test_signup_throttled_per_ip(client, db, monkeypatch):
    monkeypatch.setattr(portal, "MAX_SIGNUPS_PER_IP_HOUR", 2)
    for i in range(2):
        client.post("/candidat/inscription", data={"full_name": "X", "email": "bad"})  # rejected attempts count
    form = {"full_name": "X", "email": "ok@t.ci", "phone": "0101010101", "password": "Secret123",
            "password_confirm": "Secret123"}
    response = client.post("/candidat/inscription", data=form)
    assert response.status_code == 429 and "Trop d" in response.get_data(as_text=True)
    assert db.session.query(AuditLog).filter_by(action="signup_rejected").count() == 2


# ---------------------------------------------------------------- exports

def test_export_neutralises_spreadsheet_formulas(client, admin):
    make_candidate(full_name='=HYPERLINK("http://evil.example","clic")', city="+33 cmd", cni="-1+1")
    login(client, admin)
    sheet = pd.read_excel(io.BytesIO(client.get("/admin/exports/all?format=xlsx").data), dtype=str)
    assert sheet.loc[0, "Nom"] == '\'=HYPERLINK("http://evil.example","clic")'
    assert sheet.loc[0, "CNI"] == "'-1+1"
    csv = client.get("/admin/exports/all?format=csv").data.decode("utf-8-sig")
    assert ";\"'=HYPERLINK" in csv  # CSV quotes the cell; the apostrophe is inside


# ---------------------------------------------------------------- headers & templates

def test_security_headers(client, admin):
    login(client, admin)
    headers = client.get("/").headers
    assert "script-src 'self'" in headers["Content-Security-Policy"]
    assert "frame-ancestors 'none'" in headers["Content-Security-Policy"]
    assert headers["Cache-Control"] == "no-store"  # personal data never cached on shared PCs
    assert "Strict-Transport-Security" not in headers  # plain HTTP here
    https = client.get("/", base_url="https://localhost").headers
    assert "max-age=31536000" in https["Strict-Transport-Security"]


def test_no_inline_javascript_in_templates():
    """The CSP forbids inline scripts: any on*="..." handler or <script> body would silently break."""
    for template in (ROOT / "app" / "templates").rglob("*.html"):
        text = template.read_text(encoding="utf-8")
        assert not re.search(r"\son[a-z]+=\"", text), template.name
        assert not re.search(r"<script(?![^>]*\bsrc=)[^>]*>", text), template.name


# ---------------------------------------------------------------- deployment & data at rest

def test_web_port_only_on_localhost():
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert '"127.0.0.1:${WEB_PORT:-8000}:8000"' in compose
    assert "5432:" not in compose  # database never published


def test_stale_import_files_are_purged(client, app, admin):
    folder = app.config["UPLOAD_FOLDER"]
    stale = os.path.join(folder, "old.csv")
    with open(stale, "w", encoding="utf-8") as f:
        f.write("Nom et prénoms\nX\n")
    os.utime(stale, (time.time() - 2 * 86400,) * 2)
    login(client, admin)
    client.post("/admin/imports", data={"file": (io.BytesIO(b"Nom et prenoms\nY\n"), "c.csv")},
                content_type="multipart/form-data")
    assert not os.path.exists(stale) and len(os.listdir(folder)) == 1


def test_secrets_never_tracked_by_git():
    ignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    docker_ignore = (ROOT / ".dockerignore").read_text(encoding="utf-8")
    assert ".env" in ignore and "*.env" in ignore
    assert "**/.env" in docker_ignore
