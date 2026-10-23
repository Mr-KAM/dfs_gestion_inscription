"""Exercises: rich statement (TOAST UI Editor, Markdown + uploaded images), coach edits, history of every
answer, permission to resubmit."""
import io
from datetime import timedelta

from app.models import AuditLog, Exercise, ExerciseAnswer, Media
from app.services import campus_service
from app.services.candidate_service import BusinessError
from app.utils import utcnow
from tests.conftest import login, make_user
from tests.test_campus import _campus
from tests.test_portal import _logged_candidate

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


def test_coach_creates_with_rich_statement_and_edits(app, db, client):
    sas, a, b, coach = _campus(db)
    login(client, coach)
    page = client.get("/campus/exercises").get_data(as_text=True)
    assert "toastui-editor-all.min.js" in page and 'class="js-editor"' in page and "usageStatistics" not in page
    resp = client.post("/campus/media", data={"file": (io.BytesIO(PNG), "capture.png")},
                       content_type="multipart/form-data")
    url = resp.get_json()["url"]
    assert resp.status_code == 200 and url.startswith("/media/") and Media.query.one().mimetype == "image/png"
    statement = f"## Boucles\n\nVoir la [doc Python](https://docs.python.org/fr/3/).\n\n![schéma]({url})"
    client.post("/campus/exercises", data={"target": f"{sas.id}:Classe A", "title": "Boucles", "instructions": statement})
    e = Exercise.query.one()
    assert e.instructions == statement
    html = client.get(f"/campus/exercises/{e.id}").get_data(as_text=True)
    assert 'class="js-md md-fallback"' in html and "docs.python.org" in html

    client.post(f"/campus/exercises/{e.id}/edit", data={"title": "Boucles for", "instructions": "Nouvel énoncé",
                                                        "due_at": "2030-01-01T18:00"})
    db.session.expire_all()
    assert (e.title, e.instructions, e.due_at is not None) == ("Boucles for", "Nouvel énoncé", True)
    log = AuditLog.query.filter_by(action="exercise_update").one()
    assert set(log.details["changed"]) == {"titre", "énoncé", "date limite"}
    html = client.post(f"/campus/exercises/{e.id}/edit", data={"title": "", "instructions": "x"},
                       follow_redirects=True).get_data(as_text=True)
    assert "titre est obligatoire" in html

    other = campus_service.create_exercise(make_user("admin", "boss@test.ci"), {"target": f"{sas.id}:Classe B",
                                                                                 "title": "Autre classe"})
    assert client.post(f"/campus/exercises/{other.id}/edit", data={"title": "x"}).status_code == 403
    bad = client.post("/campus/media", data={"file": (io.BytesIO(b"<svg onload=alert(1)>"), "x.png")},
                      content_type="multipart/form-data")
    assert bad.status_code == 400 and "JPEG" in bad.get_json()["error"]


def test_student_sees_image_and_resubmits_with_permission(app, db, client):
    sas, a, b, coach = _campus(db)
    c = _logged_candidate(client, email="me@test.ci", cni="ME", phone="0303030303")
    c.sas, c.sas_class = sas, "Classe A"
    db.session.commit()
    media = campus_service.save_media(coach, io.BytesIO(PNG))
    e = campus_service.create_exercise(coach, {"target": f"{sas.id}:Classe A", "title": "Boucles",
                                               "instructions": f"![img](/media/{media.key})"})
    assert client.get(f"/media/{media.key}").data == PNG  # students can read statement images
    assert client.get("/media/" + "0" * 32).status_code == 404

    post = lambda answer: client.post("/candidat/exercices", data={"exercise_id": e.id, "answer": answer},  # noqa: E731
                                      follow_redirects=True).get_data(as_text=True)
    post("v1")
    html = post("v2")  # no permission: refused, the first answer stays
    assert "autorisée par votre coach" in html and "demandez l" in html
    campus_service.set_resubmission(e, c, coach, True)
    assert "vous autorise une nouvelle soumission" in client.get("/candidat/exercices").get_data(as_text=True)
    post("v2")
    campus_service.review(e, c, coach, "rejected", "8/20", "Revoir la boucle")  # « À refaire » allows again
    post("v3")
    campus_service.review(e, c, coach, "validated", "17/20", "Bien")
    db.session.expire_all()
    sub = e.submissions[0]
    assert [(x.number, x.answer, x.status) for x in sub.attempts] == [
        (1, "v1", "submitted"), (2, "v2", "rejected"), (3, "v3", "validated")]
    assert (sub.answer, sub.status, sub.score, sub.resubmit_allowed) == ("v3", "validated", "17/20", False)
    assert ExerciseAnswer.query.count() == 3
    html = client.get("/candidat/exercices").get_data(as_text=True)
    assert "Mes soumissions (3)" in html and "Revoir la boucle" in html and 'name="answer"' not in html


def test_permission_works_after_deadline(app, db, client):
    sas, a, b, coach = _campus(db)
    e = campus_service.create_exercise(coach, {"target": f"{sas.id}:Classe A", "title": "Vite"})
    e.due_at = utcnow() + timedelta(hours=1)
    db.session.commit()
    campus_service.submit_answer(e, a, "avant la limite")
    e.due_at = utcnow() - timedelta(minutes=1)  # deadline passed
    db.session.commit()
    try:
        campus_service.submit_answer(e, a, "en retard")
        raise AssertionError("accepted without permission")
    except BusinessError as exc:
        assert "autorisée par votre coach" in str(exc)
    campus_service.set_resubmission(e, a, coach, True)
    campus_service.submit_answer(e, a, "en retard, autorisé")
    assert [x.answer for x in e.submissions[0].attempts] == ["avant la limite", "en retard, autorisé"]
    b_sub = campus_service.can_submit(e, None)
    assert b_sub is False  # no permission, deadline passed: B cannot hand in a first answer


def test_coach_grants_from_exercise_page(app, db, client):
    sas, a, b, coach = _campus(db)
    e = campus_service.create_exercise(coach, {"target": f"{sas.id}:Classe A", "title": "Boucles"})
    campus_service.submit_answer(e, a, "ma réponse")
    login(client, coach)
    html = client.get(f"/campus/exercises/{e.id}").get_data(as_text=True)
    assert "Soumission 1" in html and "Autoriser une nouvelle soumission" in html
    client.post(f"/campus/exercises/{e.id}/resubmit/{a.id}", data={"allowed": "1"})
    db.session.expire_all()
    assert e.submissions[0].resubmit_allowed
    assert "Nouvelle soumission autorisée" in client.get(f"/campus/exercises/{e.id}").get_data(as_text=True)
    client.post(f"/campus/exercises/{e.id}/resubmit/{a.id}", data={"allowed": "0"})
    db.session.expire_all()
    assert not e.submissions[0].resubmit_allowed
    assert client.post(f"/campus/exercises/{e.id}/resubmit/{b.id}", data={"allowed": "1"},
                       follow_redirects=True).status_code == 200 and len(e.submissions) == 1  # B: other class


def test_media_upload_roles(app, db, client):
    login(client, make_user("residence", "cite@test.ci"))
    resp = client.post("/campus/media", data={"file": (io.BytesIO(PNG), "x.png")}, content_type="multipart/form-data")
    assert resp.status_code == 403 and Media.query.count() == 0
