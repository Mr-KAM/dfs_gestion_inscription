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
    new = make_candidate(email="aya@test.ci")                                  # no exam code yet
    old = eligible_candidate(email="b@t.ci", cni="B", exam_code="DFS-2026-0002")
    late = make_candidate(email="c@t.ci", cni="C")
    login(client, admin)
    resp = _upload_results(client, "email;code_examen;motivation_status;technical_status;salle;ordi;time\n"
                                   " AYA@test.ci ;DFS-2026-0001;oui;non;salle a;pc-02;45\n"
                                   "b@t.ci;DFS-2026-0002;oui;oui;;;\n"                 # no room: allowed
                                   "c@t.ci;DFS-2026-0001;oui;;;;\n"                    # code taken by line 2
                                   "c@t.ci;DFS-2026-0009;oui;oui;Salle A;;\n"          # partial room info
                                   "b@t.ci;AUTRE-1;oui;;;;\n"                          # already has another code
                                   "x@t.ci;DFS-2026-0003;peut-être;;;;\n")             # unknown email
    preview = client.get(resp.headers["Location"]).get_data(as_text=True)
    assert "Importer 2 ligne(s) valide(s)" in preview and "code examen attribué" in preview
    for msg in ("déjà attribué à KOUAME Aya", "les trois ou aucun", "a déjà le code examen DFS-2026-0002",
                "aucun candidat avec l&#39;email x@t.ci"):
        assert msg in preview, msg
    db.session.expire_all()
    assert db.session.get(Candidate, new.id).exam_code is None  # preview writes nothing

    html = client.post("/admin/imports/results/confirm").get_data(as_text=True)
    assert "2 candidat(s) mis à jour" in html and "4 ligne(s) en erreur" in html
    new = db.session.get(Candidate, new.id)
    assert new.exam_code == "DFS-2026-0001" and new.motivation_completed and new.motivation_passed
    assert new.technical_completed and new.technical_passed is False
    assert new.latest_session.workstation.computer_number == "PC-02" and new.latest_session.elapsed_seconds() == 2700
    old = db.session.get(Candidate, old.id)
    assert old.technical_completed and old.technical_passed and not old.sessions
    assert db.session.get(Candidate, late.id).exam_code is None  # bad lines change nothing


def test_results_import_admin_only(app, client, motivation_user):
    login(client, motivation_user)
    assert _upload_results(client, "email\n").status_code in (302, 403)


def test_results_confirm_without_upload(app, client, admin):
    login(client, admin)
    assert client.post("/admin/imports/results/confirm").headers["Location"].endswith("/admin/imports")


def test_completing_a_test_ticks_validated_by_default(app, db, room, motivation_user, technical_user):
    from app.services import candidate_service, timer_service
    c = make_candidate()
    candidate_service.validate_motivation(c, "P1", "DFS-2026-0001", motivation_user)
    assert c.motivation_passed and not c.technical_passed
    session = timer_service.start_test(c.id, room.id, room.workstations[0].id, 60, technical_user)
    timer_service.complete(session.id)
    assert db.session.get(Candidate, c.id).technical_passed
