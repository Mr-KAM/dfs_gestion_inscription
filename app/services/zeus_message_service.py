"""Zeus class messaging: one private conversation per student and class, between the student and the class's teaching
staff (every accepted coach, managers included) and the admin. Not real time: an unread badge, refreshed by polling.

Who may read is derived from the class at read time, never stored: a coach added to the class later sees the history,
a coach removed from it loses it at once. A student removed from the class keeps his conversation, read-only for all."""
from dataclasses import dataclass

from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import Enrollment, ZeusClass, ZeusConversation, ZeusConversationRead, ZeusMessage
from app.models.user import ROLE_ADMIN
from app.services import audit_service, zeus_service
from app.services.candidate_service import BusinessError
from app.utils import as_utc, utcnow

MAX_LENGTH = 2000
PER_PAGE = 50


# ---- access

def is_member(klass: ZeusClass, enrollment: Enrollment) -> bool:
    return any(m.enrollment_id == enrollment.id and m.joined_at for m in klass.members)


def conversation(klass: ZeusClass, enrollment: Enrollment) -> ZeusConversation | None:
    return db.session.scalar(select(ZeusConversation).where(ZeusConversation.class_id == klass.id,
                                                            ZeusConversation.enrollment_id == enrollment.id))


def _is_the_student(user, enrollment: Enrollment) -> bool:
    e = zeus_service.student_enrollment(user)
    return e is not None and e.id == enrollment.id


def can_read(user, klass: ZeusClass, enrollment: Enrollment) -> bool:
    """The student himself, or the class team (zeus_service.can_teach_class: admin, managers, accepted coaches);
    about a member of the class, or a former member whose conversation exists."""
    if enrollment.cohort_id != klass.cohort_id:
        return False
    if not (_is_the_student(user, enrollment) or zeus_service.can_teach_class(user, klass)):
        return False
    return is_member(klass, enrollment) or conversation(klass, enrollment) is not None


def can_write(user, klass: ZeusClass, enrollment: Enrollment) -> bool:
    """Only while the student is a member of the class: afterwards the conversation is read-only for everyone."""
    return can_read(user, klass, enrollment) and is_member(klass, enrollment)


# ---- writing and reading

def _set_read(conv: ZeusConversation, user, when) -> None:
    read = db.session.scalar(select(ZeusConversationRead).where(ZeusConversationRead.conversation_id == conv.id,
                                                                ZeusConversationRead.user_id == user.id))
    if read is None:
        db.session.add(ZeusConversationRead(conversation=conv, user_id=user.id, last_read_at=when))
    elif as_utc(read.last_read_at) < when:
        read.last_read_at = when


def send(klass: ZeusClass, enrollment: Enrollment, body: str, user) -> ZeusMessage:
    """The caller checked can_write. The conversation is created with its first message (never empty)."""
    body = (body or "").strip()
    if not body:
        raise BusinessError("Écrivez votre message.")
    if len(body) > MAX_LENGTH:
        raise BusinessError(f"Message trop long : {MAX_LENGTH} caractères au maximum.")
    conv = conversation(klass, enrollment)
    if conv is None:
        try:  # two first messages at once: the unique (class, student) keeps one conversation
            with db.session.begin_nested():
                conv = ZeusConversation(class_id=klass.id, enrollment_id=enrollment.id)
                db.session.add(conv)
        except IntegrityError:
            conv = conversation(klass, enrollment)
    now = utcnow()
    message = ZeusMessage(conversation=conv, author_id=user.id, body=body, created_at=now)
    db.session.add(message)
    conv.last_message_at = now
    conv.awaiting_reply = user.is_candidate
    _set_read(conv, user, now)  # one's own message is read
    db.session.commit()
    return message


def mark_read(conv: ZeusConversation, user) -> None:
    _set_read(conv, user, utcnow())
    try:
        db.session.commit()
    except IntegrityError:  # the same reader in two tabs: the other request already wrote it
        db.session.rollback()


def delete_message(message: ZeusMessage) -> ZeusConversation | None:
    """Admin moderation (the caller checks the role). Returns the conversation, or None once its last message is
    gone (no empty conversation)."""
    conv = message.conversation
    audit_service.log("zeus_message_deleted", conv, {
        "class": conv.klass.name, "student": conv.enrollment.matricule,
        "author": message.author.email if message.author else None,
        "sent_at": as_utc(message.created_at).isoformat(), "excerpt": message.body[:200]})
    conv.messages.remove(message)  # delete-orphan
    if not conv.messages:
        db.session.delete(conv)
        db.session.commit()
        return None
    last = conv.messages[-1]
    conv.last_message_at = last.created_at
    conv.awaiting_reply = bool(last.author and last.author.is_candidate)
    db.session.commit()
    return conv


def author(message: ZeusMessage, klass: ZeusClass) -> tuple[str, str]:
    """(name, function in the class) of a message's author."""
    a = message.author
    if a is None:
        return "Ancien membre de l'équipe", ""
    if a.is_candidate:
        return (a.candidate.full_name if a.candidate else a.display_name), "Étudiant(e)"
    if a.role == ROLE_ADMIN:
        return a.display_name, "Administration"
    link = zeus_service.class_link(a, klass)
    return a.display_name, (link.function if link else "Coach")


# ---- inboxes and unread

def _taught_class_ids(user) -> list[int] | None:
    """The classes whose conversations a staff member reads; None = every class (admin)."""
    if user.role == ROLE_ADMIN:
        return None
    return [link.class_id for link in user.zeus_class_links if link.accepted_at]


def inbox_classes(user) -> list[ZeusClass]:
    """Choices of the class filter of the staff inbox."""
    ids = _taught_class_ids(user)
    stmt = select(ZeusClass).order_by(ZeusClass.cohort_id.desc(), ZeusClass.name)
    if ids is not None:
        stmt = stmt.where(ZeusClass.id.in_(ids))
    return db.session.scalars(stmt).all()


def inbox(user, class_id: int | None = None, awaiting: bool = False, page: int = 1):
    """Staff inbox: the conversations of the classes one teaches (admin: all), latest first, paginated."""
    stmt = select(ZeusConversation)
    ids = _taught_class_ids(user)
    if ids is not None:
        stmt = stmt.where(ZeusConversation.class_id.in_(ids))
    if class_id:
        stmt = stmt.where(ZeusConversation.class_id == class_id)
    if awaiting:
        stmt = stmt.where(ZeusConversation.awaiting_reply.is_(True))
    stmt = stmt.order_by(ZeusConversation.last_message_at.desc(), ZeusConversation.id.desc())
    return db.paginate(stmt, page=page, per_page=PER_PAGE, error_out=False)


def last_messages(convs: list[ZeusConversation]) -> dict[int, ZeusMessage]:
    """The latest message of each conversation, in one query (inbox previews)."""
    if not convs:
        return {}
    latest = (select(func.max(ZeusMessage.id)).where(ZeusMessage.conversation_id.in_([c.id for c in convs]))
              .group_by(ZeusMessage.conversation_id))
    return {m.conversation_id: m for m in db.session.scalars(select(ZeusMessage).where(ZeusMessage.id.in_(latest)))}


def unread_ids(user, convs: list[ZeusConversation]) -> set[int]:
    """Conversations with a message the user has not seen (his own messages count as seen)."""
    if not convs:
        return set()
    reads = {r.conversation_id: as_utc(r.last_read_at) for r in db.session.scalars(select(ZeusConversationRead).where(
        ZeusConversationRead.user_id == user.id, ZeusConversationRead.conversation_id.in_([c.id for c in convs])))}
    return {c.id for c in convs if c.id not in reads or as_utc(c.last_message_at) > reads[c.id]}


@dataclass
class StudentRow:
    klass: ZeusClass
    conversation: ZeusConversation | None
    last: ZeusMessage | None
    unread: bool
    member: bool


def student_inbox(enrollment: Enrollment, user) -> list[StudentRow]:
    """One row per class of the student, and per former class whose conversation exists; latest activity first."""
    convs = {c.class_id: c for c in db.session.scalars(select(ZeusConversation).where(
        ZeusConversation.enrollment_id == enrollment.id))}
    classes = zeus_service.classes_for_student(enrollment)
    member_ids = {k.id for k in classes}
    classes += [c.klass for class_id, c in convs.items() if class_id not in member_ids]
    last, unread = last_messages(list(convs.values())), unread_ids(user, list(convs.values()))
    rows = []
    for k in classes:
        conv = convs.get(k.id)
        rows.append(StudentRow(k, conv, last.get(conv.id) if conv else None, bool(conv and conv.id in unread),
                               k.id in member_ids))
    return sorted(rows, key=lambda r: (r.conversation is None,
                                       -as_utc(r.conversation.last_message_at).timestamp() if r.conversation else 0,
                                       r.klass.name))


def unread_count(user) -> int:
    """The menu badge. Student: conversations with an unseen message. Class team: conversations where the student
    wrote last and one has not read it yet (any coach's reply clears it for the whole team). Admin: none, he reads
    every class from the inbox ("À répondre")."""
    if user.role == ROLE_ADMIN:
        return 0
    stmt = (select(func.count(ZeusConversation.id))
            .outerjoin(ZeusConversationRead, and_(ZeusConversationRead.conversation_id == ZeusConversation.id,
                                                  ZeusConversationRead.user_id == user.id))
            .where(or_(ZeusConversationRead.id.is_(None),
                       ZeusConversation.last_message_at > ZeusConversationRead.last_read_at)))
    e = zeus_service.student_enrollment(user)
    if e is not None:
        return db.session.scalar(stmt.where(ZeusConversation.enrollment_id == e.id))
    ids = _taught_class_ids(user)
    if not ids:
        return 0
    return db.session.scalar(stmt.where(ZeusConversation.class_id.in_(ids), ZeusConversation.awaiting_reply.is_(True)))
