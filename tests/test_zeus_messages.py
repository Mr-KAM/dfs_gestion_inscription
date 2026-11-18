"""Zeus class messaging: one private conversation per student and class, read by the class team and the admin."""
from types import SimpleNamespace

import pytest

from app.models import (AuditLog, Cohort, CohortCoach, ZeusClass, ZeusClassCoach, ZeusClassMember, ZeusConversation,
                        ZeusMessage)
from app.services import zeus_message_service as msgs
from app.services import zeus_service
from app.utils import utcnow
from tests.conftest import login, make_user
from tests.test_portal import portal_login
from tests.test_zeus import _student


@pytest.fixture()
def w(app, db, admin):
    """Class "Web": a manager, an assistant (accepted), a coach invited who has not answered; students ALPHA and BRAVO
    members, CHARLIE invited only. Outsiders: a coach of the cohort outside the class, the coach of class "Data"."""
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


def _url(klass, e):
    return f"/zeus/messages/{klass.id}/{e.id}"


def _as(client, user):
    client.post("/logout")
    if user.is_candidate:
        portal_login(client, user.email)
    else:
        login(client, user)


def _student_user(e):
    return e.candidate.account


def test_student_writes_and_the_class_team_answers(client, db, w):
    alpha = _student_user(w.a)
    _as(client, alpha)
    assert "Écrire à l'équipe" in client.get("/zeus/classes").get_data(as_text=True)
    response = client.post(_url(w.web, w.a), data={"body": "  Je serai absent demain.  "})
    assert response.status_code == 302 and response.location.endswith("#fin")
    conv = ZeusConversation.query.one()
    assert (conv.class_id, conv.enrollment_id, conv.awaiting_reply) == (w.web.id, w.a.id, True)
    assert conv.messages[0].body == "Je serai absent demain."  # stripped
    assert msgs.unread_count(alpha) == 0  # his own message is read

    # The class team is told (badge, JSON for the poller, home card); the admin is never badged.
    assert msgs.unread_count(w.manager) == msgs.unread_count(w.assistant) == 1
    assert msgs.unread_count(w.admin) == 0
    _as(client, w.manager)
    assert client.get("/zeus/messages/non-lus").get_json() == {"count": 1}
    assert "en attente de réponse" in client.get("/zeus/").get_data(as_text=True)
    inbox = client.get("/zeus/messages?a_repondre=1").get_data(as_text=True)
    assert "ALPHA" in inbox and "Je serai absent demain." in inbox

    # The manager reads: cleared for him only. Then he answers: cleared for the whole team, the student is told.
    assert "Je serai absent demain." in client.get(_url(w.web, w.a)).get_data(as_text=True)
    assert msgs.unread_count(w.manager) == 0 and msgs.unread_count(w.assistant) == 1
    client.post(_url(w.web, w.a), data={"body": "Bien noté, rattrapez avec BRAVO."})
    db.session.expire_all()
    assert not conv.awaiting_reply
    assert msgs.unread_count(w.assistant) == 0 and msgs.unread_count(alpha) == 1

    # The assistant (not a manager) reads and answers too.
    _as(client, w.assistant)
    client.post(_url(w.web, w.a), data={"body": "Le support est en ligne."})
    _as(client, alpha)
    assert client.get("/zeus/messages/non-lus").get_json() == {"count": 1}
    html = client.get(_url(w.web, w.a)).get_data(as_text=True)
    assert "Bien noté" in html and "Le support est en ligne." in html
    assert "Gestionnaire" in html  # each answer signed with the coach's function in the class
    assert "administration DFS" in html  # the student is told who reads
    assert msgs.unread_count(alpha) == 0
    assert [m.body for m in conv.messages][-1] == "Le support est en ligne."


def test_only_the_student_and_the_class_team_read(client, w):
    _as(client, _student_user(w.a))
    client.post(_url(w.web, w.a), data={"body": "Question privée"})
    assert client.get(_url(w.data, w.a)).status_code == 404  # not his class
    # A student can't open another student's thread, even of his own class (forged enrollment id).
    assert client.get(_url(w.web, w.b)).status_code == 404
    for outsider in (_student_user(w.b), w.invited, w.referent, w.other_coach):
        _as(client, outsider)
        assert client.get(_url(w.web, w.a)).status_code == 404, outsider.email
        assert client.post(_url(w.web, w.a), data={"body": "x"}).status_code == 404, outsider.email
        assert "Question privée" not in client.get("/zeus/messages").get_data(as_text=True), outsider.email
    assert ZeusMessage.query.count() == 1


def test_staff_start_a_conversation_with_a_member_only(client, w):
    _as(client, w.manager)
    assert f"/zeus/messages/{w.web.id}/{w.b.id}" in client.get(f"/zeus/classes/{w.web.id}").get_data(as_text=True)
    assert "Aucun message" in client.get(_url(w.web, w.b)).get_data(as_text=True)  # nothing created by opening
    assert ZeusConversation.query.count() == 0
    client.post(_url(w.web, w.b), data={"body": "Passez me voir."})
    conv = ZeusConversation.query.one()
    assert conv.enrollment_id == w.b.id and not conv.awaiting_reply
    assert msgs.unread_count(_student_user(w.b)) == 1
    assert client.get(_url(w.web, w.c)).status_code == 404  # CHARLIE is invited, not a member


def test_admin_reads_answers_and_moderates(client, db, w):
    _as(client, _student_user(w.a))
    client.post(_url(w.web, w.a), data={"body": "Premier"})
    client.post(_url(w.web, w.a), data={"body": "Second"})
    _as(client, w.admin)
    assert "ALPHA" in client.get("/zeus/messages").get_data(as_text=True)
    assert "Second" in client.get(_url(w.web, w.a)).get_data(as_text=True)
    client.post(_url(w.web, w.a), data={"body": "L'administration a pris note."})
    conv = ZeusConversation.query.one()
    first, second, reply = conv.messages
    assert not conv.awaiting_reply and msgs.unread_count(w.admin) == 0

    # Deleting the last message recomputes the conversation (the student's message is again the last one).
    client.post(f"/zeus/messages/supprimer/{reply.id}")
    db.session.expire_all()
    assert conv.awaiting_reply and [m.body for m in conv.messages] == ["Premier", "Second"]
    log = AuditLog.query.filter_by(action="zeus_message_deleted").one()
    assert log.details["excerpt"] == "L'administration a pris note."
    # Nobody else deletes.
    _as(client, w.manager)
    assert client.post(f"/zeus/messages/supprimer/{first.id}").status_code == 403
    # The last message gone, the conversation goes too (never empty).
    _as(client, w.admin)
    client.post(f"/zeus/messages/supprimer/{first.id}")
    assert client.post(f"/zeus/messages/supprimer/{second.id}", follow_redirects=True).request.path == "/zeus/messages"
    assert ZeusConversation.query.count() == 0


def test_removed_student_keeps_a_read_only_conversation(client, db, w):
    _as(client, _student_user(w.a))
    client.post(_url(w.web, w.a), data={"body": "Avant le départ"})
    db.session.delete(ZeusClassMember.query.filter_by(enrollment_id=w.a.id).one())
    db.session.commit()
    html = client.get(_url(w.web, w.a)).get_data(as_text=True)
    assert "Avant le départ" in html and "lecture seule" in html
    assert "ancienne classe" in client.get("/zeus/messages").get_data(as_text=True)
    assert client.post(_url(w.web, w.a), data={"body": "x"}).status_code == 403
    _as(client, w.manager)
    assert "lecture seule" in client.get(_url(w.web, w.a)).get_data(as_text=True)
    assert client.post(_url(w.web, w.a), data={"body": "x"}).status_code == 403
    assert ZeusMessage.query.count() == 1


def test_message_validation_and_rendering(client, w):
    _as(client, _student_user(w.a))
    for body in ("   ", "x" * (msgs.MAX_LENGTH + 1)):
        html = client.post(_url(w.web, w.a), data={"body": body}, follow_redirects=True).get_data(as_text=True)
        assert "alert-danger" in html
    assert ZeusConversation.query.count() == 0  # nothing created by a refused first message
    client.post(_url(w.web, w.a), data={"body": "<script>alert(1)</script> voir https://dfs.ci/cours"})
    html = client.get(_url(w.web, w.a)).get_data(as_text=True)
    assert "<script>alert(1)</script>" not in html and "&lt;script&gt;" in html
    assert '<a href="https://dfs.ci/cours" rel="noopener noreferrer" target="_blank">' in html


def test_badge_in_the_zeus_layout(client, w):
    _as(client, _student_user(w.a))
    client.post(_url(w.web, w.a), data={"body": "Bonjour"})
    _as(client, w.manager)
    html = client.get("/zeus/calendrier").get_data(as_text=True)
    assert 'data-unread-url="/zeus/messages/non-lus"' in html
    assert '<span data-unread-count>1</span>' in html
    client.get(_url(w.web, w.a))
    assert '<span data-unread-count>0</span>' in client.get("/zeus/calendrier").get_data(as_text=True)
