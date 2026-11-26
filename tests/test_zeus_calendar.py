"""Zeus calendar: day / week / month views, the time-grid layout and the filters. The school is in Europe/Paris
here: UTC+0 (the default, Africa/Abidjan) would hide every time-zone mistake."""
from datetime import date, datetime
from types import SimpleNamespace

import pytest
from werkzeug.datastructures import MultiDict

from app.models import (Activity, Cohort, CohortCoach, ZeusClass, ZeusClassCoach, ZeusClassMember, ZeusCourse,
                        ZeusSession)
from app.services import campus_service, settings_service, zeus_service
from app.services import zeus_calendar_service as cal
from app.utils import utcnow
from tests.conftest import login, make_user
from tests.test_portal import portal_login
from tests.test_zeus import _student

PARIS = "Europe/Paris"


def _named(role, email, name):
    user = make_user(role, email)
    user.full_name = name
    return user


@pytest.fixture()
def w(app, db, admin):
    """Cohort X (2026-2027): class Web (MANAGER manages it, TRADE teaches in it; ALPHA, BRAVO), class Data (OTHER;
    CHARLIE); REFERENT has the whole cohort. Cohort Y (2027-2028): class Agro (QUEBEC; ZULU). Reception at the desk.
    Courses: Python, SQL (X), Agri (Y)."""
    zeus_service.set_enabled(True)
    settings_service.set_many({"timezone": PARIS, "zeus_birthday_scope": "off"})
    x, y = Cohort(name="2026-2027"), Cohort(name="2027-2028")
    db.session.add_all([x, y])
    a, b = _student(db, x, "ALPHA", "26DFS000001"), _student(db, x, "BRAVO", "26DFS000002")
    c, z = _student(db, x, "CHARLIE", "26DFS000003"), _student(db, y, "ZULU", "27DFS000001")
    m, t = _named("coach", "m@test.ci", "MANAGER"), _named("trade_coach", "t@test.ci", "TRADE")
    o, q = _named("coach", "o@test.ci", "OTHER"), _named("coach", "q@test.ci", "QUEBEC")
    r = _named("coach", "r@test.ci", "REFERENT")
    reception = make_user("reception", "desk@test.ci")
    web, data = ZeusClass(cohort=x, name="Web"), ZeusClass(cohort=x, name="Data")
    agro = ZeusClass(cohort=y, name="Agro")
    python, sql = ZeusCourse(cohort=x, name="Python", hours=40), ZeusCourse(cohort=x, name="SQL", hours=20)
    agri = ZeusCourse(cohort=y, name="Agri", hours=20)
    now = utcnow()
    db.session.add_all([
        web, data, agro, python, sql, agri, CohortCoach(cohort=x, user=r, role="Coach référent"),
        ZeusClassCoach(klass=web, user=m, is_manager=True, accepted_at=now),
        ZeusClassCoach(klass=web, user=t, accepted_at=now),
        ZeusClassCoach(klass=data, user=o, is_manager=True, accepted_at=now),
        ZeusClassCoach(klass=agro, user=q, is_manager=True, accepted_at=now),
        *(ZeusClassMember(klass=k, enrollment=e, joined_at=now)
          for k, e in ((web, a), (web, b), (data, c), (agro, z)))])
    db.session.commit()
    return SimpleNamespace(admin=admin, x=x, y=y, a=a, b=b, c=c, z=z, m=m, t=t, o=o, q=q, r=r, reception=reception,
                           web=web, data=data, agro=agro, python=python, sql=sql, agri=agri,
                           alpha=a.candidate.account, charlie=c.candidate.account)


def _as(client, user):
    client.post("/logout")
    if user.is_candidate:
        portal_login(client, user.email)
    else:
        login(client, user)


def _session(db, course, klass, start, end, coach=None, room=""):
    """start / end: school time (YYYY-MM-DDTHH:MM)."""
    s = ZeusSession(course=course, klass=klass, starts_at=campus_service.local_datetime(start),
                    ends_at=campus_service.local_datetime(end), coach=coach, room=room)
    db.session.add(s)
    db.session.commit()
    return s


def _courses(client, url):
    """The sessions shown on a calendar page."""
    return client.get(url).get_data(as_text=True).count("zeus-event is-course")


# ---- views

def test_views_titles_cookie_and_old_week_links(client, w):
    _as(client, w.admin)
    page = client.get("/zeus/calendrier?vue=jour&date=2030-03-05")
    assert page.status_code == 200 and "Mardi 5 mars 2030" in page.get_data(as_text=True)
    assert client.get_cookie(cal.VIEW_COOKIE).value == "jour"
    assert "Mardi 5 mars 2030" in client.get("/zeus/calendrier?date=2030-03-05").get_data(as_text=True)  # remembered
    month = client.get("/zeus/calendrier?vue=mois&date=2030-03-05").get_data(as_text=True)
    assert "Mars 2030" in month and 'class="zeus-month"' in month
    # The old ?week= links (session page, direction calendar) still open a week, whatever the cookie says.
    assert "Semaine du 04/03 au 10/03/2030" in client.get("/zeus/calendrier?week=2030-03-05").get_data(as_text=True)
    for bad in ("0001-01-01", "9999-12-31", "2030-13-45", "x"):
        assert client.get(f"/zeus/calendrier?vue=semaine&date={bad}").status_code == 200, bad
    assert client.get("/activites/?week=0001-01-01").status_code == 200
    for user in (w.m, w.t, w.reception, w.alpha):
        _as(client, user)
        for view in ("jour", "semaine", "mois"):
            assert client.get(f"/zeus/calendrier?vue={view}&date=2030-03-05").status_code == 200, (user.email, view)


def test_parse_view_ranges(app, w):
    february = cal.parse_view(MultiDict({"vue": "mois", "date": "2027-02-10"}), None, date(2030, 1, 1))
    assert len(february.weeks) == 4 and february.days[0] == date(2027, 2, 1)  # 4 rows: starts on a Monday
    march = cal.parse_view(MultiDict({"vue": "mois", "date": "2030-03-05"}), None, date(2030, 1, 1))
    assert march.days[0] == date(2030, 2, 25) and march.days[-1] == date(2030, 3, 31)  # whole weeks
    assert (march.prev, march.next) == (date(2030, 2, 1), date(2030, 4, 1))
    week = cal.parse_view(MultiDict({"date": "2030-03-07"}), "semaine", date(2030, 1, 1))
    assert week.anchor == date(2030, 3, 4) and week.start == datetime(2030, 3, 4, tzinfo=zeus_service.school_tz())
    assert cal.parse_view(MultiDict({"date": "0001-01-01"}), "jour", date(2030, 1, 1)).anchor == date(2030, 1, 1)


# ---- layout

def _seg(start, end):
    return cal.Segment(cal.Event("course", "x"), "", start, end)


def test_layout_columns_and_hour_bounds():
    a, b, c, d = _seg(480, 600), _seg(540, 660), _seg(630, 690), _seg(780, 790)  # 8-10, 9-11, 10:30-11:30, 13-13:10
    cal.layout([a, b, c, d], 7 * 60, 20 * 60)
    assert [(s.col, s.cols) for s in (a, b, c)] == [(0, 2), (1, 2), (0, 2)]  # c reuses a's column
    assert (d.col, d.cols) == (0, 1) and round(d.height, 3) == round(30 / 780 * 100, 3)  # drawn 30 min at least
    assert round(a.top, 3) == round(60 / 780 * 100, 3)
    assert cal.hour_bounds([a, d]) == (cal.DAY_START, cal.DAY_END)
    assert cal.hour_bounds([_seg(390, 480), _seg(1260, 1335)]) == (6, 23)
    assert cal.hour_bounds([_seg(1410, 1440)]) == (cal.DAY_START, 24)


def test_days_clipping_multi_day_and_birthday_groups(client, db, w):
    """Paris time: an activity over three days goes to the all-day strip of each day (the hours stay 7-20); one that
    started before the week still shows; a session ending at midnight stays on its day."""
    db.session.add_all([
        Activity(title="Séminaire", everyone=True, starts_at=campus_service.local_datetime("2030-03-05T22:00"),
                 ends_at=campus_service.local_datetime("2030-03-07T10:00")),
        Activity(title="Retraite", everyone=True, starts_at=campus_service.local_datetime("2030-03-03T09:00"),
                 ends_at=campus_service.local_datetime("2030-03-04T12:00"))])
    db.session.commit()
    late = _session(db, w.python, w.web, "2030-03-08T22:00", "2030-03-09T00:00")
    _as(client, w.admin)
    view = cal.parse_view(MultiDict({"vue": "semaine", "date": "2030-03-05"}), None, date(2030, 1, 1))
    scope = cal.resolve_scope(w.admin, MultiDict(), zeus_service.cohorts_for(w.admin), None)
    days = {d.date: d for d in cal.by_day(cal.collect(w.admin, view, scope)[0], view)}
    labels = {n: [s.label for s in days[date(2030, 3, n)].all_day] for n in range(4, 8)}
    assert labels == {4: ["jusqu'à 12:00"], 5: ["dès 22:00"], 6: ["toute la journée"], 7: ["jusqu'à 10:00"]}
    friday = days[date(2030, 3, 8)].timed
    assert [(s.event.obj, s.start_min, s.end_min) for s in friday] == [(late, 22 * 60, 24 * 60)]
    html = client.get("/zeus/calendrier?vue=semaine&date=2030-03-05").get_data(as_text=True)
    assert "Retraite" in html and "Séminaire" in html and "toute la journée" in html
    # Three birthdays on a day: one item; two: one each.
    many = cal.Day(date(2030, 3, 5), birthdays=[cal.Event("birthday", n, day=date(2030, 3, 5)) for n in "ABC"])
    assert [s.event.title for s in many.strip] == ["3 anniversaires"]
    many.birthdays.pop()
    assert [s.event.title for s in many.strip] == ["A", "B"]


# ---- filters, roll calls, slots

def test_filters_apply_to_sessions_and_ignore_forged_values(client, db, w):
    _session(db, w.python, w.web, "2030-03-05T08:00", "2030-03-05T10:00", coach=w.r)
    _session(db, w.sql, w.data, "2030-03-05T10:00", "2030-03-05T12:00", coach=w.o)
    _session(db, w.python, None, "2030-03-06T08:00", "2030-03-06T10:00")  # whole cohort: Web attends it
    _session(db, w.agri, w.agro, "2030-03-06T08:00", "2030-03-06T10:00")
    db.session.add(Activity(title="Conseil", everyone=True, starts_at=campus_service.local_datetime("2030-03-07T08:00"),
                            ends_at=campus_service.local_datetime("2030-03-07T09:00")))
    db.session.commit()
    base = "/zeus/calendrier?vue=semaine&date=2030-03-05"

    _as(client, w.admin)
    html = client.get(f"{base}&cohort={w.x.id}&classe={w.web.id}").get_data(as_text=True)
    assert html.count("zeus-event is-course") == 2 and "<strong>SQL</strong>" not in html and "Agri" not in html
    assert "Conseil" in html and "Filtres appliqués aux séances" in html
    forged = client.get(f"{base}&cohort={w.x.id}&classe={w.agro.id}&coach={w.q.id}").get_data(as_text=True)
    assert forged.count("zeus-event is-course") == 3 and "Classe Agro" not in forged and "QUEBEC" not in forged
    assert _courses(client, f"{base}&coach={w.o.id}&cohort={w.x.id}") == 1
    assert _courses(client, base) == 4

    _as(client, w.r)  # « Mes séances »
    assert _courses(client, f"{base}&coach=moi") == 1
    _as(client, w.m)
    assert client.get(f"{base}&cohort={w.y.id}").status_code == 404
    _as(client, w.charlie)  # a student: his class and the whole cohort; another class's id is ignored
    html = client.get(f"{base}&classe={w.web.id}").get_data(as_text=True)
    assert html.count("zeus-event is-course") == 2 and "Python" in html and "Classe Web" not in html
