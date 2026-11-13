"""Two domains: the request's domain picks the site (Zeus login page), base URLs and SMTP per domain."""
import pytest

from app.admin import SETTINGS_FIELDS
from app.models import Cohort, Enrollment, Setting
from app.services import account_service, settings_service, site_service, zeus_service
from app.services.candidate_service import BusinessError
from app.utils import utcnow
from tests.conftest import PASSWORD, login, make_candidate

ZEUS, RECRUIT = "https://zeus.example.ci", "https://recrutement.example.ci"


def _save_settings(client, **changes):
    form = {k: settings_service.get(k) for k in SETTINGS_FIELDS}
    form.update(changes)
    return client.post("/admin/settings", data=form, follow_redirects=True)


def test_admin_sets_domains_and_smtp(client, admin, db):
    login(client, admin)
    html = _save_settings(client, zeus_base_url="https://zeus.example.ci/chemin").get_data(as_text=True)
    assert "URL de base Zeus invalide" in html
    html = _save_settings(client, zeus_base_url=RECRUIT, recruitment_base_url=RECRUIT).get_data(as_text=True)
    assert "doivent être différents" in html
    _save_settings(client, zeus_base_url=ZEUS + "/", recruitment_base_url=RECRUIT, smtp_zeus_host="smtp.example.ci",
                   smtp_zeus_port="465", smtp_zeus_security="ssl", smtp_zeus_sender="Zeus <zeus@example.ci>",
                   smtp_zeus_password="s3cret")
    assert settings_service.get("zeus_base_url") == ZEUS and settings_service.get("smtp_zeus_password") == "s3cret"
    assert db.session.get(Setting, "smtp_zeus_password").value != "s3cret"  # encrypted at rest
    _save_settings(client)  # password left blank: unchanged
    assert settings_service.get("smtp_zeus_password") == "s3cret"


def test_zeus_domain_has_its_own_login(app, client, admin, db):
    settings_service.set_many({"zeus_base_url": ZEUS, "recruitment_base_url": RECRUIT})
    db.session.commit()
    assert "Entrer dans Zeus" in client.get("/login", base_url=ZEUS).get_data(as_text=True)
    assert "Entrer dans Zeus" in client.get("/candidat/connexion", base_url=ZEUS).get_data(as_text=True)
    assert client.get("/candidat/inscription", base_url=ZEUS).status_code == 302  # no sign-up on Zeus
    assert "Entrer dans Zeus" not in client.get("/login", base_url=RECRUIT).get_data(as_text=True)
    assert "Entrer dans Zeus" not in client.get("/login").get_data(as_text=True)  # localhost: recruitment

    # The admin, on the Zeus domain, lands in Zeus (even with the mode off: he prepares it).
    page = client.post("/login", base_url=ZEUS, data={"email": admin.email, "password": PASSWORD},
                       follow_redirects=True)
    assert page.request.path == "/zeus/"
    client.post("/logout", base_url=ZEUS)

    # A registered student logs in once on the Zeus domain and is in Zeus.
    zeus_service.set_enabled(True)
    cohort = Cohort(name="2026-2027")
    c = make_candidate()
    db.session.add(Enrollment(cohort=cohort, candidate=c, matricule="26DFS000001", validated_at=utcnow()))
    account = account_service.sync_account(c) or c.account
    account.must_change_password = False
    db.session.commit()
    page = client.post("/candidat/connexion", base_url=ZEUS, data={"email": c.email, "password": "00000000"},
                       follow_redirects=True)
    assert page.request.path == "/zeus/" and "26DFS000001" in page.get_data(as_text=True)

    # Links shared publicly stay on the recruitment domain, whatever domain they are built from.
    with app.test_request_context("/", base_url=ZEUS):
        assert site_service.current_site() == site_service.ZEUS
        assert site_service.external_url("recruitment", "public_results.lookup", token="abc") == \
            RECRUIT + "/resultats/abc"


def test_mail_goes_through_the_domain_smtp(app, db, monkeypatch):
    sent = []

    class FakeSMTP:
        def __init__(self, host, port, timeout):
            sent.append((host, port))

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def starttls(self, context):
            sent.append("starttls")

        def login(self, user, password):
            sent.append((user, password))

        def send_message(self, msg):
            sent.append((msg["From"], msg["To"], msg["Subject"]))

    monkeypatch.setattr(site_service.smtplib, "SMTP", FakeSMTP)
    with pytest.raises(BusinessError, match="non configuré"):
        site_service.send_mail("a@test.ci", "Sujet", "Corps", site="zeus")
    settings_service.set_many({"smtp_zeus_host": "smtp.zeus.ci", "smtp_zeus_port": "587", "smtp_zeus_user": "zeus",
                               "smtp_zeus_password": "pw", "smtp_zeus_sender": "Zeus <z@zeus.ci>",
                               "smtp_zeus_security": "starttls", "smtp_recruitment_host": "smtp.dfs.ci"})
    db.session.commit()
    site_service.send_mail("a@test.ci", "Sujet", "Corps", site="zeus")
    assert sent == [("smtp.zeus.ci", 587), "starttls", ("zeus", "pw"), ("Zeus <z@zeus.ci>", "a@test.ci", "Sujet")]
