"""Zeus mode: admin switch, landing at login, classes + invitations, courses, calendar, roll calls by DFS QR code."""
from app.models import (Cohort, CohortCoach, Defense, Enrollment, Jury, JuryCandidate, Notification, Sas,
                        ZeusAttendance, ZeusClass, ZeusClassCoach, ZeusClassMember, ZeusClassRole, ZeusCourse,
                        ZeusNotification, ZeusSession)
from app.services import account_service, campus_service, defense_service, zeus_service
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

    # Mode on: one login and the coach is in Zeus (loading animation once); home always brings him back there,
    # his classic SAS pages stay reachable; his profile is shown in the Zeus layout.
    page = client.post("/login", data={"email": coach.email, "password": "Password123!"}, follow_redirects=True)
    assert page.request.path == "/zeus/" and "zeus-splash" in page.get_data(as_text=True)
    assert "zeus-splash" not in client.get("/zeus/").get_data(as_text=True)
    assert client.get("/", follow_redirects=True).request.path == "/zeus/"
    assert client.get("/campus/").status_code == 200
    assert 'class="zeus' in client.get("/profil").get_data(as_text=True)

    # Coach technique: creates a class and invites the registered students (a forged id is ignored).
    client.post(f"/zeus/cohortes/{cohort.id}/classes", data={"name": "Web"})
    klass = ZeusClass.query.one()
    assert klass.coach == coach and klass.staff[0].is_manager  # the creator manages it
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

    # Notifications: to the cohort, to a class, to one student.
    login(client, coach)
    client.post("/zeus/notifications", data={"target": f"{cohort.id}/class:{klass.id}", "title": "Salle changée"})
    client.post("/zeus/notifications", data={"target": f"{cohort.id}/student:{bravo.id}", "title": "Pour Bravo"})
    # Not registered (no DFS matricule): refused.
    client.post("/zeus/notifications", data={"target": f"{cohort.id}/student:{pending.id}", "title": "Refus-pending"})
    client.post("/logout")
    login(client, make_user("reception"))
    client.post("/zeus/notifications", data={"target": f"{cohort.id}/cohort", "title": "Rentrée lundi"})
    client.post("/logout")
    login(client, outsider)
    response = client.post("/zeus/notifications", data={"target": f"{cohort.id}/cohort", "title": "Refus-outsider"})
    assert response.status_code == 404
    client.post("/logout")
    titles = ("Salle changée", "Pour Bravo", "Rentrée lundi", "Refus-pending", "Refus-outsider")
    seen = {}
    for name in ("ALPHA", "BRAVO"):
        portal_login(client, f"{name}@test.ci")
        html = client.get("/zeus/notifications").get_data(as_text=True)
        seen[name] = {t for t in titles if t in html}
        client.post("/logout")
    assert seen == {"ALPHA": {"Salle changée", "Rentrée lundi"}, "BRAVO": {"Pour Bravo", "Rentrée lundi"}}

    # One login, one space: the student is in Zeus; the classic portal sends him back there (profile excepted).
    page = portal_login(client, "ALPHA@test.ci")
    assert page.request.path == "/zeus/" and "26DFS000001" in page.get_data(as_text=True)
    assert "Rentrée lundi" in page.get_data(as_text=True)
    for url in ("/candidat/", "/candidat/notifications", "/candidat/presence", "/candidat/exercices"):
        assert client.get(url, follow_redirects=True).request.path == "/zeus/", url
    assert 'class="zeus' in client.get("/candidat/profil").get_data(as_text=True)
    html = client.get("/zeus/appels").get_data(as_text=True)
    assert "Python" in html and "Présent(e)" in html
    client.post("/logout")

    # Every Zeus page renders for each kind of user (the cohort pages too, in the Zeus layout).
    pages = ["/zeus/", f"/zeus/cohortes/{cohort.id}", f"/zeus/cours/{course.id}", "/zeus/classes",
             f"/zeus/classes/{klass.id}", "/zeus/calendrier", f"/zeus/calendrier?cohort={cohort.id}",
             f"/zeus/seances/{s.id}", "/zeus/appels", "/zeus/notifications", "/cohortes/", f"/cohortes/{cohort.id}"]
    for user in (admin, coach):
        login(client, user)
        for url in pages:
            response = client.get(url)
            assert response.status_code == 200, (user.role, url)
        assert 'class="zeus' in client.get("/cohortes/").get_data(as_text=True)
        client.post("/logout")
    portal_login(client, "ALPHA@test.ci")
    for url in ("/zeus/", "/zeus/classes", "/zeus/calendrier?week=2026-01-05", f"/zeus/seances/{s.id}", "/zeus/appels",
                "/zeus/notifications", "/candidat/mot-de-passe"):
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


def test_registered_student_no_longer_sees_his_sas_results(app, db):
    """SAS defense result and its personal message: hidden once registered in a training cohort (Zeus user)."""
    sas, cohort = Sas(name="SAS 2026"), Cohort(name="2026-2027")
    db.session.add_all([sas, cohort])
    db.session.commit()
    alpha, charlie = (_student(db, cohort, name, matricule) for name, matricule in (("ALPHA", "26DFS000001"),
                                                                                  ("CHARLIE", None)))
    defense = Defense(sas=sas, name="Soutenance", results_sent_at=utcnow())
    jury = Jury(defense=defense, name="Jury 1")
    db.session.add_all([defense, jury])
    db.session.flush()
    for e in (alpha, charlie):
        e.candidate.sas = sas
        db.session.add(JuryCandidate(jury=jury, defense_id=defense.id, candidate=e.candidate,
                                     sheet={"decision": "admitted"}))
        db.session.add(Notification(sas=sas, candidate=e.candidate, title="Résultat", level="success"))
    db.session.add(Notification(sas=sas, title="Info SAS"))
    db.session.commit()
    assert defense_service.published_results(alpha.candidate) == []
    assert [n.title for n in campus_service.notifications_for(alpha.candidate)] == ["Info SAS"]
    assert len(defense_service.published_results(charlie.candidate)) == 1  # eligible, not registered yet
    assert {n.title for n in campus_service.notifications_for(charlie.candidate)} == {"Résultat", "Info SAS"}


def test_class_staff_roles_and_managers(app, db, client, admin):
    """Admin: class roles, edit / delete a class, coaches with a function, managers. A manager has a SAS coach's
    powers on his class and invites coaches; a plain coach of the class does roll calls and writes to it."""
    zeus_service.set_enabled(True)
    cohort = Cohort(name="2026-2027")
    db.session.add(cohort)
    alpha = _student(db, cohort, "ALPHA", "26DFS000001")
    lead, helper = make_user("coach", "lead@test.ci"), make_user("trade_coach", "helper@test.ci")

    login(client, admin)
    for name in ("Formateur principal", "Assistant"):
        client.post("/zeus/roles-de-classe", data={"name": name})
    main_role, assistant = (ZeusClassRole.query.filter_by(name=n).one() for n in ("Formateur principal", "Assistant"))
    client.post(f"/zeus/cohortes/{cohort.id}/classes", data={"name": "Web"})
    k = ZeusClass.query.one()
    assert k.coaches == []  # created by the admin: no manager yet
    client.post(f"/zeus/classes/{k.id}", data={"action": "edit", "name": "Web 1", "description": "Matin"})
    client.post(f"/zeus/classes/{k.id}", data={"action": "coach_add", "user_id": lead.id, "role_id": main_role.id,
                                               "is_manager": "1"})
    client.post(f"/zeus/cohortes/{cohort.id}/cours", data={"name": "Python", "hours": "20"})
    client.post("/logout")
    db.session.expire_all()
    assert (k.name, k.description, k.coach, k.staff[0].function) == ("Web 1", "Matin", lead, "Formateur principal")

    # The manager (no cohort-level access: the class alone opens Zeus to him) runs the class.
    login(client, lead)
    assert client.get("/zeus/").status_code == 200
    client.post(f"/zeus/classes/{k.id}", data={"action": "invite", "enrollment_ids": [alpha.id]})
    client.post(f"/zeus/classes/{k.id}", data={"action": "coach_add", "user_id": helper.id, "role_id": assistant.id,
                                               "is_manager": "1"})  # a manager cannot name a manager
    assert client.post(f"/zeus/classes/{k.id}", data={"action": "edit", "name": "X"}).status_code == 403
    assert client.post(f"/zeus/classes/{k.id}", data={"action": "delete"}).status_code == 403
    course = ZeusCourse.query.one()
    slot = {"cohort_id": cohort.id, "course_id": course.id, "starts_at": "2026-01-05T08:00", "ends_at": "2026-01-05T10:00"}
    assert client.post("/zeus/calendrier", data=slot).status_code == 403  # whole cohort: cohort level only
    client.post("/zeus/calendrier", data={**slot, "class_id": k.id})
    client.post("/logout")
    db.session.expire_all()
    invite = ZeusClassCoach.query.filter_by(user_id=helper.id).one()
    assert (invite.accepted_at, invite.is_manager, invite.function) == (None, False, "Assistant")
    s = ZeusSession.query.one()

    portal_login(client, "ALPHA@test.ci")
    client.post(f"/zeus/invitations/{ZeusClassMember.query.one().id}", data={"accept": "1"})
    client.post("/logout")

    # The invited coach answers in Zeus, then calls the roll and writes to the class, not to the whole cohort.
    login(client, helper)
    assert "Invitation à rejoindre l'équipe de la classe" in client.get("/zeus/").get_data(as_text=True)
    client.post(f"/zeus/invitations-coach/{invite.id}", data={"accept": "1"})
    assert client.post(f"/zeus/classes/{k.id}", data={"action": "invite", "enrollment_ids": []}).status_code == 403
    client.post(f"/zeus/seances/{s.id}", data={"present": [alpha.id]})
    client.post("/zeus/notifications", data={"target": f"{cohort.id}/class:{k.id}", "title": "Cours décalé"})
    client.post("/zeus/notifications", data={"target": f"{cohort.id}/cohort", "title": "Refus-cohorte"})
    client.post("/logout")
    assert ZeusAttendance.query.count() == 1
    assert [n.title for n in ZeusNotification.query] == ["Cours décalé"]

    # Admin: deleting a role keeps its coaches (no function); a class with a roll call cannot be deleted.
    login(client, admin)
    client.post("/zeus/roles-de-classe", data={"role_id": assistant.id, "delete": "1"})
    html = client.post(f"/zeus/classes/{k.id}", data={"action": "delete"}, follow_redirects=True).get_data(as_text=True)
    assert "des appels ont été faits" in html
    client.post(f"/zeus/cohortes/{cohort.id}/classes", data={"name": "Vide"})
    empty = ZeusClass.query.filter_by(name="Vide").one()
    client.post(f"/zeus/classes/{empty.id}", data={"action": "delete"})
    db.session.expire_all()
    assert ZeusClassCoach.query.filter_by(user_id=helper.id).one().function == "Coach"
    assert [c.name for c in ZeusClass.query] == ["Web 1"]
    for url in (f"/zeus/classes/{k.id}", "/zeus/roles-de-classe", "/zeus/classes", "/zeus/calendrier"):
        assert client.get(url).status_code == 200, url


def test_night_mode_switch(app, db, client, admin):
    """Night mode: header switch, kept per browser (cookie), Zeus only; the classic interface stays light."""
    login(client, admin)
    assert 'data-bs-theme="light"' in client.get("/zeus/").get_data(as_text=True)
    page = client.post("/zeus/theme", data={"theme": "dark", "next": "/zeus/classes?x=1"}, follow_redirects=True)
    assert page.request.full_path == "/zeus/classes?x=1" and 'data-bs-theme="dark"' in page.get_data(as_text=True)
    assert "Passer en mode jour" in page.get_data(as_text=True)
    assert "data-bs-theme" not in client.get("/admin/settings").get_data(as_text=True)  # classic: unchanged
    client.post("/zeus/theme", data={"theme": "light", "next": "https://evil.example/"})  # foreign next: ignored
    assert client.get_cookie("zeus_theme").value == "light"
    assert 'data-bs-theme="light"' in client.get("/zeus/").get_data(as_text=True)
