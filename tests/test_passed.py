"""'Validé' checkboxes for the motivation / technical tests in the candidate list."""
import io

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


def _upload_results(client, text):
    return client.post("/admin/imports/results", data={"file": (io.BytesIO(text.encode("utf-8-sig")), "r.csv")},
                       content_type="multipart/form-data")


def test_results_template_download(app, client, admin):
    login(client, admin)
    body = client.get("/admin/imports/results/template").get_data(as_text=True)
    assert body.lstrip("\ufeff").startswith("email;code_examen;motivation_status;technical_status;salle;ordi;time")


def test_results_import(app, db, client, admin, room):
    done = eligible_candidate(email="aya@test.ci")
    other = eligible_candidate(email="b@t.ci", cni="B", exam_code="DFS-2026-0002")
    login(client, admin)
    resp = _upload_results(client, "email;code_examen;motivation_status;technical_status;salle;ordi;time\n"
                                   "AYA@test.ci;DFS-2026-0001;oui;non;salle a;pc-02;45\n"
                                   "b@t.ci;DFS-2026-0002;oui;oui;Salle A;PC-01;\n"     # time missing
                                   "b@t.ci;DFS-2026-0001;oui;;;;\n"                     # wrong code for email
                                   "x@t.ci;DFS-2026-0002;peut-être;;;;\n")
    preview = client.get(resp.headers["Location"]).get_data(as_text=True)
    assert "Importer 1 ligne(s) valide(s)" in preview and "time" in preview and "aucun candidat" in preview
    db.session.expire_all()
    assert not db.session.get(Candidate, done.id).motivation_passed  # preview writes nothing
    assert not db.session.get(Candidate, done.id).sessions

    html = client.post("/admin/imports/results/confirm").get_data(as_text=True)
    assert "1 candidat(s) mis à jour" in html and "3 ligne(s) en erreur" in html
    done = db.session.get(Candidate, done.id)
    assert done.motivation_passed and done.technical_completed and done.technical_passed is False
    s = done.latest_session
    assert s.workstation.computer_number == "PC-02" and s.elapsed_seconds() == 45 * 60
    other = db.session.get(Candidate, other.id)
    assert not other.motivation_passed and not other.technical_completed  # bad lines change nothing


def test_results_import_admin_only(app, client, motivation_user):
    login(client, motivation_user)
    assert _upload_results(client, "email\n").status_code in (302, 403)


def test_results_confirm_without_upload(app, client, admin):
    login(client, admin)
    assert client.post("/admin/imports/results/confirm").headers["Location"].endswith("/admin/imports")
