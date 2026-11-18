"""Zeus UI: phone navigation (bottom bar per role + "Plus" sheet), the Zeus cohorts list, the roll call and the
homes' daily shortcuts."""
import re
from types import SimpleNamespace

import pytest

from app.models import Cohort, CohortCoach, ZeusClass, ZeusClassCoach, ZeusClassMember
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
