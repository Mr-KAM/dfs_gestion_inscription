"""SAS check-in: the admin opens a period for a class, students scan the rotating QR code (or the admin scans
theirs); once closed, only the admin can mark."""
import io
from datetime import timedelta

from app.models import Attendance, AttendanceSession, Sas, User
from app.services import attendance_service
from app.services.attendance_service import CODE_WINDOW, _sign
from app.services.candidate_service import BusinessError, encode_matricule
from app.utils import as_utc, utcnow
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


def test_scheduled_call_and_absence_hours(app, db, client):
    """Date / time chosen, hours counted; whoever did not mark in time gets the hours as absence hours."""
    sas = Sas(name="SAS 2026")
    c = _logged_candidate(client)
    c.sas, c.sas_class = sas, "Classe A"
    db.session.commit()
    boss = make_user("admin", "boss@test.ci")
    now = utcnow()
    missed = attendance_service.open_session(sas, "Classe A", "Lundi", 15, boss, starts_at=now - timedelta(hours=3), hours=4)
    attended = attendance_service.open_session(sas, "", "Mardi", 15, boss, starts_at=now - timedelta(hours=2), hours=7)
    attendance_service.admin_set(attended, c, True, boss)
    later = attendance_service.open_session(sas, "Classe A", "Demain", 15, boss, starts_at=now + timedelta(days=1), hours=3)
    current = attendance_service.open_session(sas, "Classe A", "Maintenant", 15, boss, hours=2)
    assert (missed.is_closed, later.is_open, later.is_closed, current.is_open) == (True, False, False, True)

    html = client.get("/candidat/").get_data(as_text=True)
    assert "Heures d'absence" in html and ">4 h<" in html  # only the missed closed call counts
    html = client.get("/candidat/presence").get_data(as_text=True)
    assert "Absent · 4 h" in html and "Présent · marqué à" in html and "Prévu le" in html
    assert "pas encore commencé" in client.post("/candidat/presence", data={"token": attendance_service.current_code(later)},
                                                follow_redirects=True).get_data(as_text=True)
    client.post("/candidat/presence", data={"token": attendance_service.current_code(current)})
    db.session.expire_all()
    mark = Attendance.query.filter_by(session_id=current.id).one()
    assert mark.marked_at is not None and attendance_service.absence_hours(c) == 4
    try:
        attendance_service.open_session(sas, "", "x", 15, boss, hours=5)
        raise AssertionError("5 h accepted")
    except BusinessError as exc:
        assert "1, 2, 3, 4, 7" in str(exc)


def test_admin_programs_call_with_date_and_hours(app, db, client, admin):
    login(client, admin)
    client.post("/admin/sas/save", data={"name": "SAS 2026"})
    sas = Sas.query.one()
    make_candidate(sas=sas, sas_class="Classe A")
    page = client.get(f"/admin/sas/{sas.id}").get_data(as_text=True)
    assert 'name="starts_at"' in page and '<option value="7">7 heures</option>' in page
    client.post("/campus/attendance", data={"target": f"{sas.id}:Classe A", "label": "Cours", "minutes": "20",
                                            "starts_at": "2026-12-01T08:00", "hours": "3"})
    s = AttendanceSession.query.one()
    assert s.hours == 3 and not s.is_open and not s.is_closed  # scheduled for later
    assert (as_utc(s.closes_at) - as_utc(s.opened_at)).total_seconds() == 20 * 60
    html = client.get(f"/campus/attendance/{s.id}").get_data(as_text=True)
    assert "Prévu" in html and "3 h" in html and "qr-lg" not in html  # QR code only while marking is open


def test_absence_hours_on_sas_page_file_and_export(app, db, client, admin):
    import pandas as pd

    login(client, admin)
    client.post("/admin/sas/save", data={"name": "SAS 2026"})
    sas = Sas.query.one()
    a = make_candidate(email="a@test.ci", cni="A", phone="0101010101", full_name="ALPHA", sas=sas, sas_class="Classe A")
    b = make_candidate(email="b@test.ci", cni="B", phone="0202020202", full_name="=BETA", sas=sas, sas_class="Classe B")
    now = utcnow()
    morning = attendance_service.open_session(sas, "Classe A", "Matin", 15, admin, starts_at=now - timedelta(hours=5), hours=4)
    all_day = attendance_service.open_session(sas, "", "Journée", 15, admin, starts_at=now - timedelta(hours=4), hours=7)
    attendance_service.admin_set(all_day, a, True, admin)
    attendance_service.open_session(sas, "", "Demain", 15, admin, starts_at=now + timedelta(days=1), hours=2)

    hours, summary, detail = attendance_service.sas_report(sas)
    assert hours == {a.id: 4, b.id: 7}  # A missed the morning (4 h), B missed the whole day (7 h)
    assert [r["Statut"] for r in detail if r["Matricule"] == a.matricule] == ["Absent", "Présent", "Prévu"]
    assert next(r for r in detail if r["Statut"] == "Présent")["Présence marquée à"]

    page = client.get(f"/admin/sas/{sas.id}").get_data(as_text=True)
    assert "<th>Absences</th>" in page and ">4 h<" in page and ">7 h<" in page
    assert ">4 h<" in client.get(f"/candidates/{a.id}").get_data(as_text=True)

    resp = client.get(f"/admin/sas/{sas.id}/attendance-export?format=xlsx")
    sheets = pd.read_excel(io.BytesIO(resp.data), sheet_name=None)
    assert list(sheets) == ["Synthèse", "Détail"] and len(sheets["Détail"]) == 5  # A: 3 calls, B: 2 (not Classe A)
    synth = sheets["Synthèse"].set_index("Matricule")
    assert synth.loc[a.matricule, "Heures d'absence"] == 4 and synth.loc[b.matricule, "Nom et prénoms"] == "'=BETA"
    csv = client.get(f"/admin/sas/{sas.id}/attendance-export").get_data(as_text=True)
    assert csv.lstrip("\ufeff").startswith("Matricule;Nom et prénoms;Classe;Appel") and "Journée" in csv
    assert morning.hours == 4
