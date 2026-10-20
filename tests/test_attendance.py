"""SAS check-in: the admin opens a period for a class, students scan the rotating QR code (or the admin scans
theirs); once closed, only the admin can mark."""
from datetime import timedelta

from app.models import Attendance, AttendanceSession, Sas, User
from app.services import attendance_service
from app.services.attendance_service import CODE_WINDOW, _sign
from app.services.candidate_service import encode_matricule
from app.utils import utcnow
from tests.conftest import login, make_candidate, make_user
from tests.test_portal import _logged_candidate


def _session(db, sas, class_name="Classe A", minutes=15):
    boss = User.query.filter_by(email="boss@test.ci").first() or make_user("admin", "boss@test.ci")
    return attendance_service.open_session(sas, class_name, "Cours du matin", minutes, boss)


def _marked(db, session):
    db.session.expire_all()
    return {m.candidate.matricule for m in db.session.query(Attendance).filter_by(session_id=session.id)}


def test_student_scans_rotating_code(app, db, client):
    sas = Sas(name="SAS 2026")
    c = _logged_candidate(client)
    c.sas, c.sas_class = sas, "Classe A"
    db.session.commit()
    s = _session(db, sas)
    assert "Pointage en cours" in client.get("/candidat/").get_data(as_text=True)
    code = attendance_service.current_code(s)
    resp = client.get(f"/candidat/presence?code={code}")
    assert "Confirmer ma présence" in resp.get_data(as_text=True) and "camera=(self)" in resp.headers["Permissions-Policy"]
    scanned_url = f"https://dfs.example/candidat/presence?code={code}"  # in-page scanner reads the whole URL
    html = client.post("/candidat/presence", data={"token": scanned_url}, follow_redirects=True).get_data(as_text=True)
    assert "Présence enregistrée" in html and _marked(db, s) == {c.matricule}
    html = client.post("/candidat/presence", data={"token": code}, follow_redirects=True).get_data(as_text=True)
    assert "déjà enregistrée" in html and "Présent ·" in html


def test_code_checks(app, db, client):
    sas = Sas(name="SAS 2026")
    c = _logged_candidate(client)
    c.sas, c.sas_class = sas, "Classe B"
    db.session.commit()
    other_class, whole_sas = _session(db, sas), _session(db, sas, class_name="")
    window = int(utcnow().timestamp()) // CODE_WINDOW
    post = lambda token: client.post("/candidat/presence", data={"token": token}, follow_redirects=True).get_data(as_text=True)
    assert "ne concerne pas votre classe" in post(attendance_service.current_code(other_class))
    assert "expiré" in post(f"{whole_sas.id}-{window - 2}-{_sign(whole_sas.id, window - 2)}")  # old photo of the QR
    assert "invalide" in post(f"{whole_sas.id}-{window}-0000000000000000")
    assert "illisible" in post("bonjour")
    whole_sas.closes_at = utcnow() - timedelta(seconds=1)  # time is up
    db.session.commit()
    assert "temps de pointage est terminé" in post(attendance_service.current_code(whole_sas))
    assert _marked(db, whole_sas) == set() and "Absent" in client.get("/candidat/presence").get_data(as_text=True)


def test_admin_flow(app, db, client, admin):
    login(client, admin)
    client.post("/admin/sas/save", data={"name": "SAS 2026"})
    sas = Sas.query.one()
    a = make_candidate(email="a@test.ci", cni="A", full_name="ALPHA", sas=sas, sas_class="Classe A")
    b = make_candidate(email="b@test.ci", cni="B", full_name="BETA", sas=sas, sas_class="Classe A")
    out = make_candidate(email="c@test.ci", cni="C", full_name="GAMMA", sas=sas, sas_class="Classe B")
    resp = client.post("/campus/attendance", data={"label": "Cours", "target": f"{sas.id}:Classe A", "minutes": "15"})
    s = AttendanceSession.query.one()
    page = client.get(resp.headers["Location"])
    html = page.get_data(as_text=True)
    assert "<svg" in html and "ALPHA" in html and "GAMMA" not in html and "camera=(self)" in page.headers["Permissions-Policy"]
    assert "En attente" in client.get(f"/campus/attendance/{s.id}?partial=1").get_data(as_text=True)

    client.post(f"/campus/attendance/{s.id}/close")
    assert "Absent" in client.get(f"/campus/attendance/{s.id}?partial=1").get_data(as_text=True)
    html = client.post(f"/campus/attendance/{s.id}/scan", data={"token": encode_matricule(a.matricule)},
                       follow_redirects=True).get_data(as_text=True)  # closed: the admin can still mark
    assert "ALPHA : présent(e)" in html
    html = client.post(f"/campus/attendance/{s.id}/scan", data={"token": encode_matricule(out.matricule)},
                       follow_redirects=True).get_data(as_text=True)
    assert "ne fait pas partie de ce pointage" in html
    client.post(f"/campus/attendance/{s.id}/mark/{b.id}", data={"present": "1"})
    assert _marked(db, s) == {a.matricule, b.matricule}
    client.post(f"/campus/attendance/{s.id}/mark/{a.id}", data={"present": "0"})
    assert _marked(db, s) == {b.matricule}
    assert "1 présent(s)" in client.get(f"/admin/sas/{sas.id}").get_data(as_text=True)


def test_attendance_admin_only(app, db, client, motivation_user):
    sas = Sas(name="SAS 2026")
    db.session.add(sas)
    db.session.commit()
    login(client, motivation_user)
    assert client.post("/campus/attendance", data={"target": f"{sas.id}:", "minutes": "15"}).status_code == 403
    assert AttendanceSession.query.count() == 0
