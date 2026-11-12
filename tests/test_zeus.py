"""Zeus mode: admin switch, landing at login, classes + invitations, courses, calendar, roll calls by DFS QR code."""
from app.models import (Cohort, CohortCoach, Enrollment, ZeusAttendance, ZeusClass, ZeusClassMember, ZeusCourse,
                        ZeusSession)
from app.services import account_service, zeus_service
from app.services.candidate_service import decode_matricule, encode_matricule
from app.utils import utcnow
from tests.conftest import login, make_candidate, make_user
from tests.test_portal import portal_login


def _student(db, cohort, name, matricule=None):
    c = make_candidate(full_name=name, email=f"{name}@test.ci", cni=name, phone=f"07{abs(hash(name)) % 10**8:08d}")
    e = Enrollment(cohort=cohort, candidate=c, matricule=matricule, validated_at=utcnow() if matricule else None)
    db.session.add(e)
    account = account_service.sync_account(c) or c.account
    account.must_change_password = False
    db.session.commit()
    return e


def test_dfs_matricule_qr_round_trip(app):
    assert decode_matricule(encode_matricule("26DFS000001")) == "26DFS000001"
    assert decode_matricule(encode_matricule("26CAND000001")) == "26CAND000001"


def test_zeus_flow(app, db, client, admin):
    cohort, other = Cohort(name="2026-2027"), Cohort(name="2027-2028")
    db.session.add_all([cohort, other])
    alpha = _student(db, cohort, "ALPHA", "26DFS000001")
    bravo = _student(db, cohort, "BRAVO", "26DFS000002")
    pending = _student(db, cohort, "CHARLIE")  # eligible, not registered: never in Zeus
    coach, outsider = make_user("coach"), make_user("coach", "outsider@test.ci")
    db.session.add_all([CohortCoach(cohort=cohort, user=coach, role="Coach référent"),
                        CohortCoach(cohort=other, user=outsider, role="Tuteur")])
    db.session.commit()

    # Mode off: only the admin enters (to prepare); coaches stay in the classic interface.
    login(client, coach)
    assert client.get("/zeus/").status_code == 302
    client.post("/logout")
    login(client, admin)
    assert "Mode Zeus désactivé" in client.get("/zeus/").get_data(as_text=True)
    client.post("/zeus/mode", data={"enabled": "1"})
    assert zeus_service.enabled()
    client.post("/logout")

    # Mode on: the coach lands in Zeus at login, with the loading animation once; then the classic one stays reachable.
    page = client.post("/login", data={"email": coach.email, "password": "Password123!"}, follow_redirects=True)
    assert page.request.path == "/zeus/" and "zeus-splash" in page.get_data(as_text=True)
    assert "zeus-splash" not in client.get("/zeus/").get_data(as_text=True)
    assert client.get("/").status_code in (200, 302) and client.get("/", follow_redirects=True).request.path != "/zeus/"

    # Coach technique: creates a class and invites the registered students (a forged id is ignored).
    client.post(f"/zeus/cohortes/{cohort.id}/classes", data={"name": "Web"})
    klass = ZeusClass.query.one()
    assert klass.coach_id == coach.id
    client.post(f"/zeus/classes/{klass.id}", data={"action": "invite",
                                                   "enrollment_ids": [alpha.id, bravo.id, pending.id]})
    assert {m.enrollment_id for m in ZeusClassMember.query} == {alpha.id, bravo.id}
    # Course and session (past, so attendance counts).
    client.post(f"/zeus/cohortes/{cohort.id}/cours", data={"name": "Python", "hours": "40", "coach_id": coach.id})
    course = ZeusCourse.query.one()
    client.post("/zeus/calendrier", data={"cohort_id": cohort.id, "course_id": course.id, "class_id": klass.id,
                                          "starts_at": "2026-01-05T08:00", "ends_at": "2026-01-05T12:00", "room": "S1"})
    s = ZeusSession.query.one()
    assert (s.hours, s.coach_id, course.scheduled_hours) == (4, coach.id, 4)
    assert "Python" in client.get("/zeus/calendrier?week=2026-01-05").get_data(as_text=True)
    client.post("/logout")

    # Students answer the invitations from their Zeus space.
    portal_login(client, "ALPHA@test.ci")
    member = ZeusClassMember.query.filter_by(enrollment_id=alpha.id).one()
    client.post(f"/zeus/invitations/{member.id}", data={"accept": "1"})
    client.post("/logout")
    portal_login(client, "BRAVO@test.ci")
    member = ZeusClassMember.query.filter_by(enrollment_id=bravo.id).one()
    client.post(f"/zeus/invitations/{member.id}", data={"accept": "0"})
    assert client.get(f"/zeus/seances/{s.id}").status_code == 404  # not in the class
    client.post("/logout")
    db.session.expire_all()
    assert [e.id for e in s.expected] == [alpha.id]

    # Roll call: DFS QR code scanned, then the manual list.
    login(client, coach)
    html = client.post(f"/zeus/seances/{s.id}/scan", data={"token": encode_matricule(bravo.matricule)},
                       follow_redirects=True).get_data(as_text=True)
    assert "non attendu" in html
    client.post(f"/zeus/seances/{s.id}/scan", data={"token": encode_matricule(alpha.matricule)})
    assert ZeusAttendance.query.one().method == "scan"
    client.post(f"/zeus/seances/{s.id}", data={})  # nobody ticked: absent
    assert ZeusAttendance.query.count() == 0
    client.post(f"/zeus/seances/{s.id}", data={"present": [alpha.id, bravo.id]})  # bravo not expected: ignored
    assert [m.enrollment_id for m in ZeusAttendance.query] == [alpha.id]
    client.post("/logout")

    portal_login(client, "ALPHA@test.ci")
    html = client.get("/zeus/appels").get_data(as_text=True)
    assert "Python" in html and "Présent(e)" in html
    assert "26DFS000001" in client.get("/zeus/").get_data(as_text=True)
    client.post("/logout")

    # Every Zeus page renders for each kind of user (the cohort pages too, in the Zeus layout).
    pages = ["/zeus/", f"/zeus/cohortes/{cohort.id}", f"/zeus/cours/{course.id}", "/zeus/classes",
             f"/zeus/classes/{klass.id}", "/zeus/calendrier", f"/zeus/calendrier?cohort={cohort.id}",
             f"/zeus/seances/{s.id}", "/zeus/appels", "/cohortes/", f"/cohortes/{cohort.id}"]
    for user in (admin, coach):
        login(client, user)
        for url in pages:
            response = client.get(url)
            assert response.status_code == 200, (user.role, url)
        assert 'class="zeus' in client.get("/cohortes/").get_data(as_text=True)
        client.post("/logout")
    portal_login(client, "ALPHA@test.ci")
    for url in ("/zeus/", "/zeus/classes", "/zeus/calendrier?week=2026-01-05", f"/zeus/seances/{s.id}", "/zeus/appels"):
        assert client.get(url).status_code == 200, url
    client.post("/logout")

    # A coach of another cohort cannot touch this one; a coach without cohort and a tester never enter Zeus.
    login(client, outsider)
    assert client.post(f"/zeus/cohortes/{cohort.id}/classes", data={"name": "X"}).status_code == 404
    assert client.get(f"/zeus/seances/{s.id}").status_code == 404
    client.post("/logout")
    for role in ("trade_coach", "technical_tester"):
        login(client, make_user(role))
        assert client.get("/zeus/").status_code == 403
        client.post("/logout")
