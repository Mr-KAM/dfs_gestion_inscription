"""Zeus UI: phone navigation (bottom bar per role + "Plus" sheet), the Zeus cohorts list, the roll call and the
homes' daily shortcuts."""
import re
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from app.models import (Cohort, CohortCoach, ZeusAttendance, ZeusClass, ZeusClassCoach, ZeusClassMember, ZeusCourse,
                        ZeusSession)
from app.services import zeus_service
from app.utils import utcnow
from tests.conftest import login, make_user
from tests.test_portal import portal_login
from tests.test_zeus import _student


@pytest.fixture()
def w(app, db, admin):
    """Cohort 2026-2027, class Web: MANAGER (a coach of the class only), TRADE (trade coach); students ALPHA and
    BRAVO. REFERENT: a coach the admin gave the cohort to. Reception at the desk."""
    zeus_service.set_enabled(True)
    cohort = Cohort(name="2026-2027")
    db.session.add(cohort)
    a = _student(db, cohort, "ALPHA", "26DFS000001")
    b = _student(db, cohort, "BRAVO", "26DFS000002")
    manager, trade = make_user("coach", "m@test.ci"), make_user("trade_coach", "t@test.ci")
    referent, reception = make_user("coach", "r@test.ci"), make_user("reception", "desk@test.ci")
    web = ZeusClass(cohort=cohort, name="Web")
    now = utcnow()
    db.session.add_all([web, ZeusClassCoach(klass=web, user=manager, is_manager=True, accepted_at=now),
                        ZeusClassCoach(klass=web, user=trade, accepted_at=now),
                        CohortCoach(cohort=cohort, user=referent, role="Coach référent"),
                        ZeusClassMember(klass=web, enrollment=a, joined_at=now),
                        ZeusClassMember(klass=web, enrollment=b, joined_at=now)])
    db.session.commit()
    return SimpleNamespace(admin=admin, cohort=cohort, a=a, b=b, manager=manager, trade=trade, referent=referent,
                           reception=reception, web=web, alpha=a.candidate.account)


def _as(client, user):
    client.post("/logout")
    if user.is_candidate:
        portal_login(client, user.email)
    else:
        login(client, user)


def _part(html, start, end):
    """The markup between two markers (the bottom bar, the "Plus" sheet)."""
    i = html.index(start)
    return html[i:html.index(end, i)]


def _bar(html):
    return _part(html, '<nav class="bottom-nav', "</nav>")


def _sheet(html):
    return _part(html, 'id="zeus-more"', '<div id="copy-status"')


def _labels(markup):
    return re.findall(r"<span>([^<]+)</span>", markup)


def test_bottom_bar_and_plus_sheet_per_role(client, w):
    _as(client, w.alpha)
    html = client.get("/zeus/").get_data(as_text=True)
    assert _labels(_bar(html)) == ["Accueil", "Présences", "Messages", "Calendrier", "Plus"]
    assert {"Mes classes", "Notifications", "Mon profil", "Mot de passe"} <= set(_labels(_sheet(html)))
    assert "Déconnexion" in _sheet(html) and "Mode nuit" in _sheet(html)

    _as(client, w.manager)
    html = client.get("/zeus/").get_data(as_text=True)
    assert _labels(_bar(html)) == ["Accueil", "Appels", "Classes", "Messages", "Plus"]
    assert {"Cohortes", "Calendrier", "Notifications"} <= set(_labels(_sheet(html)))
    assert "Classique" in _sheet(html)  # staff: the classic interface from the sheet too

    _as(client, w.admin)
    html = client.get("/zeus/").get_data(as_text=True)
    assert _labels(_bar(html)) == ["Accueil", "Cohortes", "Classes", "Calendrier", "Plus"]
    assert {"Appels", "Messages", "Rôles de classe"} <= set(_labels(_sheet(html)))

    _as(client, w.reception)  # the desk never has conversations: no Messages anywhere
    html = client.get("/zeus/").get_data(as_text=True)
    assert _labels(_bar(html)) == ["Accueil", "Cohortes", "Classes", "Calendrier", "Plus"]
    assert 'href="/zeus/messages"' not in html and "data-unread-badge" not in html  # nothing to poll either


def test_plus_is_highlighted_for_the_pages_it_holds(client, w):
    _as(client, w.alpha)
    plus = 'class="bottom-nav-item active" data-bs-toggle="offcanvas"'
    assert plus not in client.get("/zeus/").get_data(as_text=True)
    html = client.get("/zeus/notifications").get_data(as_text=True)
    assert plus in html and 'class="zeus-tile active"' in html


def test_zeus_cohorts_list(client, w):
    detail = f'href="/cohortes/{w.cohort.id}"'  # the classic enrollment page
    _as(client, w.manager)  # a coach of a class only: sees its cohort, not the enrollment page he can't open
    assert "2026-2027" in client.get("/zeus/cohortes").get_data(as_text=True)
    assert detail not in client.get(f"/zeus/cohortes/{w.cohort.id}").get_data(as_text=True)
    for user in (w.referent, w.admin):
        _as(client, user)
        assert detail in client.get(f"/zeus/cohortes/{w.cohort.id}").get_data(as_text=True), user.email
    _as(client, w.alpha)
    assert client.get("/zeus/cohortes").status_code == 404


def test_class_birthdays_in_the_zeus_layout(client, w):
    _as(client, w.manager)
    html = client.get(f"/zeus/classes/{w.web.id}/anniversaires").get_data(as_text=True)
    assert 'class="bottom-nav' in html and "data-print" in html


def _live_session(db, w, name="Python"):
    now = utcnow()
    s = ZeusSession(course=ZeusCourse(cohort=w.cohort, name=name), klass=w.web, coach=w.manager,
                    starts_at=now - timedelta(minutes=1), ends_at=now + timedelta(hours=1))
    db.session.add(s)
    db.session.commit()
    return s


def test_roll_call_rows_and_absences(client, db, w):
    s = _live_session(db, w)
    _as(client, w.manager)
    html = client.get(f"/zeus/seances/{s.id}").get_data(as_text=True)
    assert html.count('class="zeus-call-item"') == html.count('name="present"') == 2  # one big row per student
    assert "data-call-count" in html and "data-call-all" in html and 'data-bs-target="#call-scan"' in html
    client.post(f"/zeus/seances/{s.id}", data={"present": [w.a.id]})
    assert [m.enrollment_id for m in ZeusAttendance.query] == [w.a.id]
    assert f'value="{w.a.id}" checked' in client.get(f"/zeus/seances/{s.id}").get_data(as_text=True)

    _as(client, w.reception)  # read-only: who is missing, not only a number
    html = client.get(f"/zeus/seances/{s.id}").get_data(as_text=True)
    absent = html[html.index("Absent(e)s"):]
    assert "BRAVO" in absent and "ALPHA" not in absent and 'name="present"' not in html


def test_home_sessions_today_first(client, db, w):
    _live_session(db, w)
    week = zeus_service.week_start(None)
    past = (datetime.now(week.tzinfo).date() - week.date()).days >= 1  # not on a Monday: a session earlier this week
    if past:
        db.session.add(ZeusSession(course=ZeusCourse(cohort=w.cohort, name="Histoire"), klass=w.web,
                                   starts_at=week + timedelta(hours=8), ends_at=week + timedelta(hours=10)))
        db.session.commit()
    _as(client, w.manager)
    html = client.get("/zeus/").get_data(as_text=True)
    assert "Faire l'appel" in html and "zeus-session is-live" in html and "en cours" in html
    if past:
        assert "Histoire" in html[html.index("zeus-session-past"):]

    _as(client, w.alpha)  # the student: his session, no roll-call button; his QR code first
    html = client.get("/zeus/").get_data(as_text=True)
    assert "Python" in html and "Faire l'appel" not in html
    assert html.index("Mon QR code DFS") < html.index("Mon inscription") and 'id="qr-modal"' in html


def test_tables_become_rows_on_phones(client, db, w):
    _live_session(db, w)
    _as(client, w.manager)
    for path in ("/zeus/appels", f"/zeus/classes/{w.web.id}", f"/zeus/cohortes/{w.cohort.id}"):
        html = client.get(path).get_data(as_text=True)
        assert "zeus-rows" in html and "zr-line" in html, path
