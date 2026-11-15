"""Direction (DG, DGA): read-only statistics (recruitment, SAS, selection juries, cohorts) and the activities
calendar, where they say who is concerned."""
from app.models import Activity, Cohort, Defense, Jury, JuryCandidate, Sas
from app.services import candidate_service, zeus_service
from app.utils import utcnow
from tests.conftest import login, make_candidate, make_user
from tests.test_portal import portal_login
from tests.test_zeus import _student


def test_direction_reads_every_statistic(app, db, client):
    sas, cohort = Sas(name="SAS 2026"), Cohort(name="2026-2027")
    defense = Defense(sas=sas, name="Soutenance finale")
    jury = Jury(defense=defense, name="Jury 1", validated_at=utcnow())
    db.session.add_all([sas, cohort, defense, jury])
    db.session.flush()
    db.session.add(JuryCandidate(jury=jury, defense_id=defense.id, candidate=make_candidate(sas=sas),
                                 sheet={"decision": "admitted"}))
    _student(db, cohort, "Ama", "26DFS000001")
    dg = make_user("dg", "dg@test.ci")

    login(client, dg)
    html = client.get("/").get_data(as_text=True)
    for text in ("Direction générale", "Total candidats", "SAS 2026", "Soutenance finale", "2026-2027",
                 "1 / 1 inscrits"):
        assert text in html
    assert "/admin/" not in html  # read only: no admin link
    assert client.get(f"/cohortes/{cohort.id}").status_code == 200
    assert client.post("/cohortes/", data={"name": "X"}).status_code == 403
    assert client.get(f"/cohortes/inscription/{cohort.enrollments[0].id}").status_code == 403
    assert client.get("/candidates/").status_code == 403 and client.get("/admin/exports").status_code == 403


def test_activities_for_the_people_concerned(app, db, client):
    zeus_service.set_enabled(True)
    student = _student(db, Cohort(name="2026-2027"), "Ama", "26DFS000001")
    dg, coach, reception, supervisor = (make_user(r, f"{r}@test.ci") for r in ("dga", "coach", "reception",
                                                                                "supervisor"))
    day = candidate_service.today().isoformat()

    login(client, dg)
    client.post("/activites/", data={"title": "Sans public", "starts_at": f"{day}T08:00", "ends_at": f"{day}T09:00"})
    assert db.session.query(Activity).count() == 0  # who is concerned: required
    client.post("/activites/", data={"title": "Conseil pédagogique", "starts_at": f"{day}T10:00",
                                     "ends_at": f"{day}T12:00", "place": "Salle B", "roles": ["coach", "candidate"],
                                     "user_ids": [reception.id]})
    assert "Conseil pédagogique" in client.get("/activites/").get_data(as_text=True)
    client.post("/logout")

    for user, sees in ((coach, True), (reception, True), (supervisor, False)):
        login(client, user)
        assert ("Conseil pédagogique" in client.get("/activites/").get_data(as_text=True)) is sees
        client.post("/logout")
    login(client, supervisor)
    assert client.post("/activites/", data={"title": "X"}).status_code == 403  # only the direction and the admin
    client.post("/logout")

    portal_login(client, student.candidate.email)  # a registered student: in his Zeus calendar too
    assert "Conseil pédagogique" in client.get("/zeus/calendrier").get_data(as_text=True)
