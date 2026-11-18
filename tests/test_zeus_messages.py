"""Zeus class messaging: private one-to-one conversations between a student and one coach of his class; the admin
reviews them, read-only; sending and reviewing are audit logged."""
import json
from types import SimpleNamespace

import pytest

from app.models import (AuditLog, Cohort, CohortCoach, ZeusClass, ZeusClassCoach, ZeusClassMember, ZeusConversation,
                        ZeusConversationRead, ZeusMessage)
from app.services import zeus_message_service as msgs
from app.services import zeus_service
from app.utils import utcnow
from tests.conftest import login, make_user
from tests.test_portal import portal_login
from tests.test_zeus import _student


@pytest.fixture()
def w(app, db, admin):
    """Class "Web": a manager (coach 1), an assistant (coach 2, accepted), a coach invited who has not answered;
    students ALPHA and BRAVO members, CHARLIE invited only. Outsiders: a coach of the cohort outside the class, the
    coach of class "Data"."""
    zeus_service.set_enabled(True)
    cohort = Cohort(name="2026-2027")
    db.session.add(cohort)
    a = _student(db, cohort, "ALPHA", "26DFS000001")
    b = _student(db, cohort, "BRAVO", "26DFS000002")
    c = _student(db, cohort, "CHARLIE", "26DFS000003")
    manager, assistant = make_user("coach", "manager@test.ci"), make_user("trade_coach", "assistant@test.ci")
    invited, referent = make_user("coach", "invited@test.ci"), make_user("coach", "referent@test.ci")
    other_coach = make_user("coach", "other@test.ci")
    web, data = ZeusClass(cohort=cohort, name="Web"), ZeusClass(cohort=cohort, name="Data")
    now = utcnow()
    db.session.add_all([
        web, data,
        ZeusClassCoach(klass=web, user=manager, is_manager=True, accepted_at=now),
        ZeusClassCoach(klass=web, user=assistant, accepted_at=now),
        ZeusClassCoach(klass=web, user=invited),
        ZeusClassCoach(klass=data, user=other_coach, is_manager=True, accepted_at=now),
        CohortCoach(cohort=cohort, user=referent, role="Coach référent"),
        ZeusClassMember(klass=web, enrollment=a, joined_at=now),
        ZeusClassMember(klass=web, enrollment=b, joined_at=now),
        ZeusClassMember(klass=web, enrollment=c),
    ])
    db.session.commit()
    return SimpleNamespace(admin=admin, cohort=cohort, a=a, b=b, c=c, manager=manager, assistant=assistant,
                           invited=invited, referent=referent, other_coach=other_coach, web=web, data=data)


def _url(klass, e, coach):
    return f"/zeus/messages/{klass.id}/{e.id}/{coach.id}"


def _as(client, user):
    client.post("/logout")
    if user.is_candidate:
        portal_login(client, user.email)
    else:
        login(client, user)


def _student_user(e):
    return e.candidate.account


def test_student_writes_to_one_coach_only(client, db, w):
    alpha = _student_user(w.a)
    _as(client, alpha)
    assert "Écrire à un coach" in client.get("/zeus/classes").get_data(as_text=True)
    inbox = client.get("/zeus/messages").get_data(as_text=True)
    assert _url(w.web, w.a, w.manager) in inbox and _url(w.web, w.a, w.assistant) in inbox  # one row per coach
    assert _url(w.web, w.a, w.invited) not in inbox  # an invitation not answered yet
    url = _url(w.web, w.a, w.manager)
    response = client.post(url, data={"body": "  Je serai absent demain.  "})
    assert response.status_code == 302 and response.location.endswith("#fin")
    conv = ZeusConversation.query.one()
    assert (conv.class_id, conv.enrollment_id, conv.coach_id, conv.awaiting_reply) == (
        w.web.id, w.a.id, w.manager.id, True)
    assert conv.messages[0].body == "Je serai absent demain."  # stripped
    assert msgs.unread_count(alpha) == 0  # his own message is read

    # Only the coach written to is told; the admin is never badged.
    assert msgs.unread_count(w.manager) == 1
    assert msgs.unread_count(w.assistant) == msgs.unread_count(w.admin) == 0
    # Audit logged, without the text.
    log = AuditLog.query.filter_by(action="zeus_message_sent").one()
    assert (log.user_id, log.details["coach"], log.details["student"]) == (alpha.id, "manager@test.ci", "26DFS000001")
    assert "absent" not in json.dumps(log.details)

    # The other coach of the class doesn't see it.
    _as(client, w.assistant)
    assert "Je serai absent" not in client.get("/zeus/messages").get_data(as_text=True)
    assert client.get(url).status_code == 404

    # The coach: badge, home card, inbox; he reads, then answers.
    _as(client, w.manager)
    assert client.get("/zeus/messages/non-lus").get_json() == {"count": 1}
    assert "en attente de réponse" in client.get("/zeus/").get_data(as_text=True)
    inbox = client.get("/zeus/messages?a_repondre=1").get_data(as_text=True)
    assert "ALPHA" in inbox and "Je serai absent demain." in inbox
    assert "Je serai absent demain." in client.get(url).get_data(as_text=True)
    assert msgs.unread_count(w.manager) == 0
    client.post(url, data={"body": "Bien noté, rattrapez avec BRAVO."})
    db.session.expire_all()
    assert not conv.awaiting_reply and msgs.unread_count(alpha) == 1

    _as(client, alpha)
    html = client.get(url).get_data(as_text=True)
    assert "Bien noté" in html and "Gestionnaire" in html  # signed with the coach's function in the class
    assert "administration DFS" in html  # the student is told who may read
    assert msgs.unread_count(alpha) == 0
    assert AuditLog.query.filter_by(action="zeus_message_sent").count() == 2


def test_one_conversation_per_coach(client, w):
    alpha = _student_user(w.a)
    _as(client, alpha)
    client.post(_url(w.web, w.a, w.manager), data={"body": "Question A"})
    client.post(_url(w.web, w.a, w.assistant), data={"body": "Question B"})
    assert ZeusConversation.query.count() == 2
    assert msgs.unread_count(w.manager) == msgs.unread_count(w.assistant) == 1

    _as(client, w.assistant)
    html = client.get(_url(w.web, w.a, w.assistant)).get_data(as_text=True)
    assert "Question B" in html and "Question A" not in html
    client.post(_url(w.web, w.a, w.assistant), data={"body": "Réponse B"})
    assert msgs.unread_count(w.manager) == 1  # still owes his own answer
    assert msgs.unread_count(alpha) == 1

    _as(client, alpha)
    inbox = client.get("/zeus/messages").get_data(as_text=True)
    assert "Réponse B" in inbox and "Question A" in inbox  # one row per coach


def test_only_the_participants_read(client, w):
    _as(client, _student_user(w.a))
    url = _url(w.web, w.a, w.manager)
    client.post(url, data={"body": "Question privée"})
    assert client.get(_url(w.data, w.a, w.other_coach)).status_code == 404  # not his class
    assert client.get(_url(w.web, w.b, w.manager)).status_code == 404  # another student's (forged enrollment)
    assert client.get(_url(w.web, w.a, w.referent)).status_code == 404  # not a coach of the class (forged coach)
    assert client.post(_url(w.web, w.a, w.invited), data={"body": "x"}).status_code == 404  # invitation pending
    for outsider in (_student_user(w.b), w.assistant, w.invited, w.referent, w.other_coach):
        _as(client, outsider)
        assert client.get(url).status_code == 404, outsider.email
        assert client.post(url, data={"body": "x"}).status_code == 404, outsider.email
        assert "Question privée" not in client.get("/zeus/messages").get_data(as_text=True), outsider.email
    assert ZeusMessage.query.count() == 1


def test_admin_reviews_read_only(client, w):
    _as(client, _student_user(w.a))
    url = _url(w.web, w.a, w.manager)
    client.post(url, data={"body": "Premier"})
    _as(client, w.assistant)
    client.post(_url(w.web, w.b, w.assistant), data={"body": "Passez me voir"})

    _as(client, w.admin)
    inbox = client.get("/zeus/messages").get_data(as_text=True)
    assert "Revue des messages" in inbox and "ALPHA" in inbox and "BRAVO" in inbox
    by_coach = client.get(f"/zeus/messages?coach={w.assistant.id}").get_data(as_text=True)
    assert "BRAVO" in by_coach and "ALPHA" not in by_coach
    assert "ALPHA" in client.get(f"/zeus/messages?classe={w.web.id}&coach={w.manager.id}").get_data(as_text=True)

    html = client.get(url).get_data(as_text=True)
    assert "Premier" in html and "journal d'audit" in html and 'name="body"' not in html
    client.get(url)
    reviews = AuditLog.query.filter_by(action="zeus_conversation_review").all()
    assert len(reviews) == 2  # every opening
    assert (reviews[0].user_id, reviews[0].details["coach"], reviews[0].details["student"]) == (
        w.admin.id, "manager@test.ci", "26DFS000001")

    assert client.post(url, data={"body": "x"}).status_code == 403  # never writes
    assert client.post("/zeus/messages/supprimer/1").status_code == 404  # nor deletes: no such route
    assert ZeusMessage.query.count() == 2
    assert msgs.unread_count(w.manager) == 1  # the review moves no one's read marker
    assert ZeusConversationRead.query.filter_by(user_id=w.admin.id).count() == 0
    assert msgs.unread_count(w.admin) == 0
    assert client.get(_url(w.web, w.b, w.manager)).status_code == 404  # no such conversation: nothing to review


def test_coach_starts_a_conversation_with_a_member_only(client, w):
    _as(client, w.manager)
    assert _url(w.web, w.b, w.manager) in client.get(f"/zeus/classes/{w.web.id}").get_data(as_text=True)
    assert "Aucun message" in client.get(_url(w.web, w.b, w.manager)).get_data(as_text=True)  # opening creates none
    assert ZeusConversation.query.count() == 0
    client.post(_url(w.web, w.b, w.manager), data={"body": "Passez me voir."})
    conv = ZeusConversation.query.one()
    assert (conv.enrollment_id, conv.coach_id, conv.awaiting_reply) == (w.b.id, w.manager.id, False)
    assert msgs.unread_count(_student_user(w.b)) == 1
    assert client.get(_url(w.web, w.c, w.manager)).status_code == 404  # CHARLIE is invited, not a member
    assert client.get(_url(w.web, w.b, w.assistant)).status_code == 404  # one writes in his own name only

    _as(client, w.admin)  # the admin's class page: the review, no chat button
    page = client.get(f"/zeus/classes/{w.web.id}").get_data(as_text=True)
    assert "Revue des messages" in page and f"/zeus/messages/{w.web.id}/" not in page


def test_removed_coach_loses_access(client, db, w):
    alpha = _student_user(w.a)
    _as(client, alpha)
    url = _url(w.web, w.a, w.manager)
    client.post(url, data={"body": "Avant le départ"})
    # The manager also teaches "Data": he stays a Zeus user, only "Web" is taken from him.
    db.session.add(ZeusClassCoach(klass=w.data, user=w.manager, accepted_at=utcnow()))
    db.session.commit()
    zeus_service.remove_coach(ZeusClassCoach.query.filter_by(class_id=w.web.id, user_id=w.manager.id).one())

    _as(client, w.manager)
    assert client.get(url).status_code == 404
    assert "Avant le départ" not in client.get("/zeus/messages").get_data(as_text=True)
    assert msgs.unread_count(w.manager) == 0

    _as(client, alpha)
    html = client.get(url).get_data(as_text=True)
    assert "Avant le départ" in html and "ne fait plus partie de l'équipe" in html
    assert "Ancien coach de la classe" in client.get("/zeus/messages").get_data(as_text=True)
    assert client.post(url, data={"body": "x"}).status_code == 403

    _as(client, w.admin)
    assert client.get(url).status_code == 200  # still reviewed
    assert ZeusMessage.query.count() == 1


def test_removed_student_keeps_a_read_only_conversation(client, db, w):
    _as(client, _student_user(w.a))
    url = _url(w.web, w.a, w.manager)
    client.post(url, data={"body": "Avant le départ"})
    db.session.delete(ZeusClassMember.query.filter_by(enrollment_id=w.a.id).one())
    db.session.commit()
    html = client.get(url).get_data(as_text=True)
    assert "Avant le départ" in html and "lecture seule" in html
    assert "ancienne classe" in client.get("/zeus/messages").get_data(as_text=True)
    assert client.post(url, data={"body": "x"}).status_code == 403
    assert client.get(_url(w.web, w.a, w.assistant)).status_code == 404  # nor a new one
    _as(client, w.manager)
    assert "lecture seule" in client.get(url).get_data(as_text=True)
    assert client.post(url, data={"body": "x"}).status_code == 403
    assert ZeusMessage.query.count() == 1


def test_message_validation_and_rendering(client, w):
    _as(client, _student_user(w.a))
    url = _url(w.web, w.a, w.manager)
    for body in ("   ", "x" * (msgs.MAX_LENGTH + 1)):
        html = client.post(url, data={"body": body}, follow_redirects=True).get_data(as_text=True)
        assert "alert-danger" in html
    assert ZeusConversation.query.count() == 0  # nothing created by a refused first message
    assert AuditLog.query.filter_by(action="zeus_message_sent").count() == 0
    client.post(url, data={"body": "<script>alert(1)</script> voir https://dfs.ci/cours"})
    html = client.get(url).get_data(as_text=True)
    assert "<script>alert(1)</script>" not in html and "&lt;script&gt;" in html
    assert '<a href="https://dfs.ci/cours" rel="noopener noreferrer" target="_blank">' in html


def test_badge_in_the_zeus_layout(client, w):
    _as(client, _student_user(w.a))
    client.post(_url(w.web, w.a, w.manager), data={"body": "Bonjour"})
    _as(client, w.manager)
    html = client.get("/zeus/calendrier").get_data(as_text=True)
    assert 'data-unread-url="/zeus/messages/non-lus"' in html
    assert '<span data-unread-count>1</span>' in html
    client.get(_url(w.web, w.a, w.manager))
    assert '<span data-unread-count>0</span>' in client.get("/zeus/calendrier").get_data(as_text=True)
