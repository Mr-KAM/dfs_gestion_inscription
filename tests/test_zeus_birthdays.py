"""Zeus birthdays: who sees whose birthday (the admin's level), the opt-out, the home prompt, wishes, the calendar,
the class page and its monthly list. Only the day and month are ever shown."""
import json
from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from app.models import AuditLog, Cohort, ZeusBirthdayWish, ZeusClass, ZeusClassCoach, ZeusClassMember
from app.services import settings_service, zeus_service
from app.services import zeus_birthday_service as bd
from app.utils import utcnow
from tests.conftest import login, make_user
from tests.test_portal import portal_login
from tests.test_zeus import _student

WEDNESDAY = date(2026, 11, 18)  # ALPHA's birthday; BRAVO's falls on Saturday 21/11


def _staff(role, email, name, day=None, month=None):
    user = make_user(role, email)
    user.full_name, user.birthday_day, user.birthday_month = name, day, month
    return user


def _on(monkeypatch, day):
    monkeypatch.setattr(bd, "today", lambda: day)


def _level(db, scope, wishes=True):
    settings_service.set_many({"zeus_birthday_scope": scope, "zeus_birthday_wishes": "1" if wishes else "0"})
    db.session.commit()


def _names(user):
    return {p.name for p in bd.circle(user)}


def _as(client, user):
    client.post("/logout")
    if user.is_candidate:
        portal_login(client, user.email)
    else:
        login(client, user)


@pytest.fixture()
def w(app, db, admin, monkeypatch):
    """Cohort X: class Web (manager MANAGER, coach TRADE; students ALPHA, BRAVO), class Data (OTHER; CHARLIE).
    Cohort Y: class Agro (QUEBEC; ZULU). Reception at the desk. Today: Wednesday 18/11/2026."""
    zeus_service.set_enabled(True)
    x, y = Cohort(name="2026-2027"), Cohort(name="2027-2028")
    db.session.add_all([x, y])
    a = _student(db, x, "ALPHA", "26DFS000001")
    b = _student(db, x, "BRAVO", "26DFS000002")
    c = _student(db, x, "CHARLIE", "26DFS000003")
    z = _student(db, y, "ZULU", "27DFS000001")
    m = _staff("coach", "m@test.ci", "MANAGER", 25, 11)
    t = _staff("trade_coach", "t@test.ci", "TRADE", 2, 12)
    o = _staff("coach", "o@test.ci", "OTHER", 18, 11)
    q = _staff("coach", "q@test.ci", "QUEBEC", 20, 11)
    reception = make_user("reception", "desk@test.ci")
    web, data = ZeusClass(cohort=x, name="Web"), ZeusClass(cohort=x, name="Data")
    agro = ZeusClass(cohort=y, name="Agro")
    now = utcnow()
    db.session.add_all([
        web, data, agro,
        ZeusClassCoach(klass=web, user=m, is_manager=True, accepted_at=now),
        ZeusClassCoach(klass=web, user=t, accepted_at=now),
        ZeusClassCoach(klass=data, user=o, is_manager=True, accepted_at=now),
        ZeusClassCoach(klass=agro, user=q, is_manager=True, accepted_at=now),
        ZeusClassMember(klass=web, enrollment=a, joined_at=now),
        ZeusClassMember(klass=web, enrollment=b, joined_at=now),
        ZeusClassMember(klass=data, enrollment=c, joined_at=now),
        ZeusClassMember(klass=agro, enrollment=z, joined_at=now),
    ])
    a.candidate.birth_date, b.candidate.birth_date = date(1998, 11, 18), date(1998, 11, 21)
    c.candidate.birth_date, z.candidate.birth_date = date(1998, 12, 1), date(1998, 11, 19)
    db.session.commit()
    _on(monkeypatch, WEDNESDAY)
    return SimpleNamespace(admin=admin, x=x, y=y, a=a, b=b, c=c, z=z, m=m, t=t, o=o, q=q, reception=reception,
                           web=web, data=data, agro=agro, alpha=a.candidate.account, bravo=b.candidate.account,
                           charlie=c.candidate.account)


def test_dates():
    assert bd.occurrence(29, 2, 2027) == date(2027, 2, 28) and bd.occurrence(29, 2, 2028) == date(2028, 2, 29)
    assert bd.window(WEDNESDAY) == (WEDNESDAY, WEDNESDAY)
    weekend = (date(2026, 11, 20), date(2026, 11, 23))  # Friday to Monday
    assert bd.window(date(2026, 11, 21)) == bd.window(date(2026, 11, 22)) == weekend
    new_year = bd.Person("x", "X", 1, 1, None, "")  # 1/1/2028 is a Saturday: celebrated from 31/12/2027
    assert bd.celebrated([new_year], date(2027, 12, 31)) == [(new_year, date(2028, 1, 1))]
    assert bd.celebrated([new_year], date(2028, 1, 3)) == [(new_year, date(2028, 1, 1))]
    assert bd.celebrated([new_year], date(2028, 1, 4)) == []
    assert bd.day_month(date(2026, 6, 1)) == "1er juin" and bd.day_month(date(2026, 12, 12)) == "12 décembre"
    assert bd.month_start("2026-13", WEDNESDAY) == bd.month_start(None, WEDNESDAY) == date(2026, 11, 1)
    assert bd.month_start("2027-02", WEDNESDAY) == date(2027, 2, 1)


def test_who_sees_whom(db, w):
    assert bd.scope() == "class"  # the default
    assert _names(w.alpha) == {"BRAVO", "MANAGER", "TRADE"}  # his class: classmates and their coaches
    assert _names(w.m) == {"ALPHA", "BRAVO", "TRADE"}
    assert _names(w.o) == {"CHARLIE"}
    assert "ZULU" in _names(w.admin) and _names(w.reception) == set()

    _level(db, "cohort")
    assert _names(w.alpha) == {"BRAVO", "CHARLIE", "MANAGER", "TRADE", "OTHER"}  # not cohort Y
    assert _names(w.q) == {"ZULU"}
    assert _names(w.admin) == {"ALPHA", "BRAVO", "CHARLIE", "ZULU", "MANAGER", "TRADE", "OTHER", "QUEBEC"}
    assert _names(w.reception) == set()

    _level(db, "off")
    assert _names(w.alpha) == _names(w.admin) == set()
    assert bd.class_people(w.web) == []


def test_hidden_birthday_and_never_the_year(client, db, w):
    _as(client, w.m)
    calendar = client.get("/zeus/calendrier?week=2026-11-16").get_data(as_text=True)
    assert "ALPHA" in calendar and "1998" not in calendar
    monthly = client.get(f"/zeus/classes/{w.web.id}/anniversaires?mois=2026-11").get_data(as_text=True)
    assert "<td>18 novembre</td><td>ALPHA</td>" in monthly and "1998" not in monthly

    w.alpha.hide_birthday = True
    db.session.commit()
    assert "ALPHA" not in _names(w.m) and "ALPHA" not in _names(w.bravo)
    assert all(p.name != "ALPHA" for p in bd.class_people(w.web))
    assert "ALPHA" not in client.get("/zeus/calendrier?week=2026-11-16").get_data(as_text=True)
    assert "ALPHA" not in client.get(f"/zeus/classes/{w.web.id}/anniversaires?mois=2026-11").get_data(as_text=True)
    _as(client, w.bravo)
    assert "Anniversaires du jour" not in client.get("/zeus/").get_data(as_text=True)


def test_home_prompt(client, db, w, monkeypatch):
    newbie = _staff("coach", "new@test.ci", "NEW")
    db.session.add(ZeusClassCoach(klass=w.web, user=newbie, accepted_at=utcnow()))
    db.session.commit()
    _as(client, newbie)
    assert "Quand est votre anniversaire" in client.get("/zeus/").get_data(as_text=True)
    client.post("/zeus/anniversaire", data={"action": "later"})
    assert "Quand est votre anniversaire" not in client.get("/zeus/").get_data(as_text=True)
    _on(monkeypatch, WEDNESDAY + timedelta(days=31))
    assert "Quand est votre anniversaire" in client.get("/zeus/").get_data(as_text=True)  # asked again

    html = client.post("/zeus/anniversaire", data={"action": "save", "birthday_day": "31", "birthday_month": "2"},
                       follow_redirects=True).get_data(as_text=True)
    assert "alert-danger" in html
    client.post("/zeus/anniversaire", data={"action": "save", "birthday_day": "29", "birthday_month": "2"})
    db.session.expire_all()
    assert (newbie.birthday_day, newbie.birthday_month) == (29, 2)
    assert "Quand est votre anniversaire" not in client.get("/zeus/").get_data(as_text=True)

    # A student gives his full birth date (his record).
    w.b.candidate.birth_date = None
    db.session.commit()
    _as(client, w.bravo)
    home = client.get("/zeus/").get_data(as_text=True)
    assert "Quand est votre anniversaire" in home and 'name="birth_date"' in home
    client.post("/zeus/anniversaire", data={"action": "save", "birth_date": "2001-03-04"})
    db.session.expire_all()
    assert w.b.candidate.birth_date == date(2001, 3, 4)

    # Hiding it is an answer too; and nobody is asked while birthdays are off.
    shy = _staff("trade_coach", "shy@test.ci", "SHY")
    db.session.add(ZeusClassCoach(klass=w.web, user=shy, accepted_at=utcnow()))
    db.session.commit()
    _as(client, shy)
    client.post("/zeus/anniversaire", data={"action": "save", "hide_birthday": "1"})
    db.session.expire_all()
    assert shy.hide_birthday and "Quand est votre anniversaire" not in client.get("/zeus/").get_data(as_text=True)
    _level(db, "off")
    shy.hide_birthday = False
    db.session.commit()
    assert not bd.needs_prompt(shy, WEDNESDAY)
    assert not bd.needs_prompt(w.reception, WEDNESDAY)  # the desk is not asked


def test_wishes(client, db, w, monkeypatch):
    _as(client, w.bravo)
    home = client.get("/zeus/").get_data(as_text=True)
    assert "Anniversaires du jour" in home and f"/zeus/anniversaire/voeux/{w.alpha.id}" in home
    client.post(f"/zeus/anniversaire/voeux/{w.alpha.id}", data={"body": "  Joyeux anniversaire Alpha !  "})
    wish = ZeusBirthdayWish.query.one()
    assert (wish.author_id, wish.recipient_id, wish.birthday, wish.body) == (
        w.bravo.id, w.alpha.id, WEDNESDAY, "Joyeux anniversaire Alpha !")
    html = client.post(f"/zeus/anniversaire/voeux/{w.alpha.id}", data={"body": "Encore"},
                       follow_redirects=True).get_data(as_text=True)
    assert "alert-danger" in html and ZeusBirthdayWish.query.count() == 1  # one per author and birthday
    assert "Vœu envoyé" in client.get("/zeus/").get_data(as_text=True)
    log = AuditLog.query.filter_by(action="zeus_birthday_wish").one()
    assert log.details["recipient"] == w.alpha.email and "Joyeux" not in json.dumps(log.details)

    # Outside one's circle: 404. Not (or no longer) the day: refused.
    _as(client, w.charlie)
    assert client.post(f"/zeus/anniversaire/voeux/{w.alpha.id}", data={"body": "x"}).status_code == 404
    _as(client, w.m)
    client.post(f"/zeus/anniversaire/voeux/{w.bravo.id}", data={"body": "Trop tôt"})  # Saturday's, from Friday
    assert ZeusBirthdayWish.query.count() == 1

    # The weekend birthday: from Friday to Monday.
    _on(monkeypatch, date(2026, 11, 23))
    home = client.get("/zeus/").get_data(as_text=True)
    assert "BRAVO" in home and "ce week-end (21 novembre)" in home
    client.post(f"/zeus/anniversaire/voeux/{w.bravo.id}", data={"body": "Bon anniversaire"})
    assert ZeusBirthdayWish.query.filter_by(recipient_id=w.bravo.id).one().birthday == date(2026, 11, 21)
    _on(monkeypatch, WEDNESDAY)

    # The recipient reads and deletes; nobody else can.
    _as(client, w.alpha)
    home = client.get("/zeus/").get_data(as_text=True)
    assert "Joyeux anniversaire, ALPHA" in home and "Joyeux anniversaire Alpha !" in home
    _as(client, w.m)
    assert client.post(f"/zeus/anniversaire/voeux/{wish.id}/supprimer").status_code == 404
    _as(client, w.alpha)
    client.post(f"/zeus/anniversaire/voeux/{wish.id}/supprimer")
    assert ZeusBirthdayWish.query.filter_by(recipient_id=w.alpha.id).count() == 0

    # The admin's switch: no button, refused.
    _level(db, "class", wishes=False)
    _as(client, w.bravo)
    assert "Souhaiter son anniversaire" not in client.get("/zeus/").get_data(as_text=True)
    client.post(f"/zeus/anniversaire/voeux/{w.alpha.id}", data={"body": "x"})
    assert ZeusBirthdayWish.query.filter_by(recipient_id=w.alpha.id).count() == 0

    # Purged after a month.
    db.session.add(ZeusBirthdayWish(recipient_id=w.alpha.id, author_id=w.m.id, birthday=date(2026, 10, 1),
                                    body="Vieux", created_at=utcnow() - timedelta(days=31)))
    db.session.commit()
    bd.wishes_received(w.alpha, WEDNESDAY)
    assert ZeusBirthdayWish.query.filter_by(body="Vieux").count() == 0


def test_calendar_class_card_and_monthly_list(client, w):
    _as(client, w.m)
    calendar = client.get("/zeus/calendrier?week=2026-11-16").get_data(as_text=True)
    assert calendar.count("zeus-event is-birthday") == 2  # ALPHA (18/11), BRAVO (21/11); himself not
    page = client.get(f"/zeus/classes/{w.web.id}").get_data(as_text=True)
    assert "Prochains anniversaires" in page
    assert "21 novembre" in page and "25 novembre" in page and "2 décembre" in page  # next 30 days, himself too
    monthly = client.get(f"/zeus/classes/{w.web.id}/anniversaires?mois=2026-12").get_data(as_text=True)
    assert "<td>2 décembre</td><td>TRADE</td>" in monthly and "ALPHA" not in monthly
    assert "novembre 2026" in client.get(f"/zeus/classes/{w.web.id}/anniversaires?mois=2026-13").get_data(as_text=True)
    for outsider in (w.o, w.alpha):  # another class's coach, a student
        _as(client, outsider)
        assert client.get(f"/zeus/classes/{w.web.id}/anniversaires").status_code == 404, outsider.email


def test_settings_admin_only_and_profiles(client, db, w):
    _as(client, w.m)
    assert client.post("/zeus/anniversaire/reglages", data={"scope": "cohort", "wishes": "1"}).status_code == 403
    _as(client, w.admin)
    assert 'name="scope"' in client.get("/zeus/").get_data(as_text=True)
    client.post("/zeus/anniversaire/reglages", data={"scope": "cohort"})
    assert bd.scope() == "cohort" and not bd.wishes_enabled()
    log = AuditLog.query.filter_by(action="settings_update").one()
    assert log.details == {"zeus_birthday_scope": "cohort", "zeus_birthday_wishes": False}
    client.post("/zeus/anniversaire/reglages", data={"scope": "everyone"})
    assert bd.scope() == "cohort"  # unknown level refused

    # Staff profile: day and month, the opt-out; half a date is refused.
    _as(client, w.o)
    form = {"action": "profile", "full_name": "OTHER", "birthday_prefs": "1", "birthday_day": "3",
            "birthday_month": "4", "hide_birthday": "1"}
    client.post("/profil", data=form)
    db.session.expire_all()
    assert (w.o.birthday_day, w.o.birthday_month, w.o.hide_birthday) == (3, 4, True)
    html = client.post("/profil", data=form | {"birthday_month": ""}, follow_redirects=True).get_data(as_text=True)
    assert "alert-danger" in html

    # Student profile: the opt-out only where it is shown.
    _as(client, w.alpha)
    assert "Ne pas afficher mon anniversaire dans Zeus" in client.get("/candidat/profil").get_data(as_text=True)
    form = {"phone": w.a.candidate.phone, "birth_date": "1998-11-18"}
    client.post("/candidat/profil", data=form | {"birthday_prefs": "1", "hide_birthday": "1"})
    db.session.expire_all()
    assert w.alpha.hide_birthday
    client.post("/candidat/profil", data=form)  # a form without the box keeps the choice
    db.session.expire_all()
    assert w.alpha.hide_birthday and w.a.candidate.birth_date == date(1998, 11, 18)
