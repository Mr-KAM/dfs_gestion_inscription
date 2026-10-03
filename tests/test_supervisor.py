import pytest

from app.models import Candidate, TechnicalTestSession, User
from app.models.user import ROLE_SUPERVISOR
from tests.conftest import login, make_candidate, make_user


@pytest.fixture()
def supervisor(app):
    return make_user(ROLE_SUPERVISOR)


def test_supervisor_does_motivation_then_technical(client, db, room, supervisor):
    c = make_candidate()
    login(client, supervisor)
    assert client.get(f"/candidates/{c.id}").status_code == 200  # sees non-eligible candidates too
    client.post(f"/motivation/{c.id}/validate", data={"piece_number": "P1", "exam_code": "DFS-S1"})
    assert db.session.get(Candidate, c.id).motivation_completed

    ws = room.workstations[0]
    client.post(f"/technical/start/{c.id}", data={"room_id": room.id, "workstation_id": ws.id, "duration_minutes": 30})
    session = db.session.query(TechnicalTestSession).one()
    assert session.status == "running" and session.technical_tester_id == supervisor.id
    for action in ("pause", "resume", "complete"):
        client.post(f"/technical/session/{session.id}/{action}")
    assert db.session.get(Candidate, c.id).technical_completed


def test_supervisor_pages_render(client, room, supervisor):
    login(client, supervisor)
    for url in ("/", "/?partial=1", "/candidates/", "/motivation/", "/technical/sessions", "/technical/rooms"):
        assert client.get(url).status_code == 200, url
    page = client.get("/").get_data(as_text=True)
    assert "Supervision" in page and "Utilisateurs" not in page


def test_supervisor_has_no_admin_rights(client, db, supervisor):
    c = make_candidate()
    login(client, supervisor)
    for url in ("/admin/users", "/admin/users/new", "/admin/rooms", "/admin/kobo/", "/admin/imports",
                "/admin/exports", "/admin/exports/all", "/admin/audit", "/admin/settings",
                "/candidates/new", f"/candidates/{c.id}/edit"):
        assert client.get(url).status_code == 403, url
    assert client.post(f"/candidates/{c.id}/delete").status_code == 403
    assert client.post("/admin/users/new", data={"email": "x@t.ci", "role": "admin", "password": "Secret123!"}).status_code == 403
    assert client.post("/admin/kobo/sync").status_code == 403
    assert db.session.query(User).count() == 1 and db.session.get(Candidate, c.id)


def test_supervisor_cannot_rewrite_validated_motivation(client, db, supervisor):
    c = make_candidate()
    login(client, supervisor)
    client.post(f"/motivation/{c.id}/validate", data={"piece_number": "P1", "exam_code": "DFS-S1"})
    client.post(f"/motivation/{c.id}/validate", data={"piece_number": "P2", "exam_code": "DFS-S2"})
    assert db.session.get(Candidate, c.id).exam_code == "DFS-S1"  # admin-only correction


def test_admin_can_create_supervisor(client, admin, db):
    login(client, admin)
    assert "Superviseur" in client.get("/admin/users/new").get_data(as_text=True)
    client.post("/admin/users/new", data={"email": "sup@t.ci", "role": "supervisor", "password": "Secret123!"})
    assert db.session.query(User).filter_by(email="sup@t.ci").one().role == ROLE_SUPERVISOR
