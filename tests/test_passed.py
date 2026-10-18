"""'Validé' checkboxes for the motivation / technical tests in the candidate list."""
from app.models import Candidate
from tests.conftest import eligible_candidate, login, make_candidate


def test_tick_and_untick_motivation(app, db, client, motivation_user):
    c = eligible_candidate()
    login(client, motivation_user)
    client.post(f"/candidates/{c.id}/passed/motivation", data={"passed": "1", "next": "/candidates/"})
    assert db.session.get(Candidate, c.id).motivation_passed
    resp = client.post(f"/candidates/{c.id}/passed/motivation", data={"next": "//evil.com"})
    assert db.session.get(Candidate, c.id).motivation_passed is False
    assert "evil.com" not in resp.headers["Location"]


def test_requires_completed_test_and_role(app, db, client, motivation_user):
    pending, eligible = make_candidate(), eligible_candidate(email="b@t.ci", cni="B")
    login(client, motivation_user)
    client.post(f"/candidates/{pending.id}/passed/motivation", data={"passed": "1"})
    assert not db.session.get(Candidate, pending.id).motivation_passed  # interview not done yet
    assert client.post(f"/candidates/{eligible.id}/passed/technical", data={"passed": "1"}).status_code == 403
    assert client.post(f"/candidates/{eligible.id}/passed/other", data={"passed": "1"}).status_code == 404


def test_list_shows_checkboxes(app, client, admin):
    eligible_candidate()
    login(client, admin)
    html = client.get("/candidates/").get_data(as_text=True)
    assert html.count("js-autosubmit") == 2
