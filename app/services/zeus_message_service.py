"""Zeus class messaging: private one-to-one conversations between a student and one coach of one of his classes.
The other coaches of the class never see them. The admin reviews every conversation, read-only: each review is audit
logged, as is each message sent (never its text). Not real time: an unread badge, refreshed by polling.

Who may read is derived from the class at read time, never stored: a coach removed from the class loses his
conversations at once (the student keeps them, read-only; the admin still reviews them). A student removed from the
class keeps his conversations, read-only for everyone."""
from dataclasses import dataclass

from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import Enrollment, User, ZeusClass, ZeusConversation, ZeusConversationRead, ZeusMessage
from app.models.user import ROLE_ADMIN
from app.services import audit_service, zeus_service
from app.services.candidate_service import BusinessError
from app.utils import as_utc, utcnow

MAX_LENGTH = 2000
PER_PAGE = 50


# ---- access

def is_member(klass: ZeusClass, enrollment: Enrollment) -> bool:
    return any(m.enrollment_id == enrollment.id and m.joined_at for m in klass.members)


def on_team(klass: ZeusClass, coach: User) -> bool:
    """An accepted coach of the class, managers and trade coaches included (a pending invitation is not)."""
    return zeus_service.class_link(coach, klass) is not None


def conversation(klass: ZeusClass, enrollment: Enrollment, coach: User) -> ZeusConversation | None:
    return db.session.scalar(select(ZeusConversation).where(ZeusConversation.class_id == klass.id,
                                                            ZeusConversation.enrollment_id == enrollment.id,
                                                            ZeusConversation.coach_id == coach.id))


def _is_the_student(user, enrollment: Enrollment) -> bool:
    e = zeus_service.student_enrollment(user)
    return e is not None and e.id == enrollment.id


def is_reviewer(user) -> bool:
    """Reads every conversation, never writes: the admin."""
    return user.role == ROLE_ADMIN


def is_participant(user, klass: ZeusClass, enrollment: Enrollment, coach: User) -> bool:
    """The student himself, or the coach himself while he is on the class team."""
    return _is_the_student(user, enrollment) or (user.id == coach.id and on_team(klass, coach))


def can_read(user, klass: ZeusClass, enrollment: Enrollment, coach: User) -> bool:
    """A participant: about a member of the class and a coach of its team (to start one), or once the conversation
    exists. The reviewer: an existing conversation."""
    if enrollment.cohort_id != klass.cohort_id:
        return False
    if is_participant(user, klass, enrollment, coach):
        return ((is_member(klass, enrollment) and on_team(klass, coach))
                or conversation(klass, enrollment, coach) is not None)
    return is_reviewer(user) and conversation(klass, enrollment, coach) is not None


def can_write(user, klass: ZeusClass, enrollment: Enrollment, coach: User) -> bool:
    """The two participants, while the student is a member of the class and the coach on its team: afterwards the
    conversation is read-only. Never the reviewer."""
    return (enrollment.cohort_id == klass.cohort_id and is_participant(user, klass, enrollment, coach)
            and is_member(klass, enrollment) and on_team(klass, coach))


# ---- writing and reading

def _set_read(conv: ZeusConversation, user, when) -> None:
    read = db.session.scalar(select(ZeusConversationRead).where(ZeusConversationRead.conversation_id == conv.id,
                                                                ZeusConversationRead.user_id == user.id))
    if read is None:
        db.session.add(ZeusConversationRead(conversation=conv, user_id=user.id, last_read_at=when))
    elif as_utc(read.last_read_at) < when:
        read.last_read_at = when


def _audit_details(conv: ZeusConversation) -> dict:
    return {"conversation": conv.id, "class": conv.klass.name, "student": conv.enrollment.matricule,
            "coach": conv.coach.email}


def send(klass: ZeusClass, enrollment: Enrollment, coach: User, body: str, user) -> ZeusMessage:
    """The caller checked can_write. The conversation is created with its first message (never empty). Audit logged
    without the text: the conversation itself is the record."""
    body = (body or "").strip()
    if not body:
        raise BusinessError("Écrivez votre message.")
    if len(body) > MAX_LENGTH:
        raise BusinessError(f"Message trop long : {MAX_LENGTH} caractères au maximum.")
    conv = conversation(klass, enrollment, coach)
    if conv is None:
        try:  # two first messages at once: the unique (class, student, coach) keeps one conversation
            with db.session.begin_nested():
                conv = ZeusConversation(class_id=klass.id, enrollment_id=enrollment.id, coach_id=coach.id)
                db.session.add(conv)
        except IntegrityError:
            conv = conversation(klass, enrollment, coach)
    now = utcnow()
    message = ZeusMessage(conversation=conv, author_id=user.id, body=body, created_at=now)
    db.session.add(message)
    conv.last_message_at = now
    conv.awaiting_reply = user.is_candidate
    _set_read(conv, user, now)  # one's own message is read
    db.session.flush()  # ids for the audit row
    audit_service.log("zeus_message_sent", message, _audit_details(conv) | {"author": user.email}, user=user)
    db.session.commit()
    return message


def mark_read(conv: ZeusConversation, user) -> None:
    _set_read(conv, user, utcnow())
    try:
        db.session.commit()
    except IntegrityError:  # the same reader in two tabs: the other request already wrote it
        db.session.rollback()


def review(conv: ZeusConversation, user) -> None:
    """The admin opens a conversation he is not part of: audit logged, every time. Nobody's read marker moves."""
    audit_service.log("zeus_conversation_review", conv, _audit_details(conv), user=user)
    db.session.commit()


def author(message: ZeusMessage, klass: ZeusClass) -> tuple[str, str]:
    """(name, function in the class) of a message's author."""
    a = message.author
    if a is None:
        return "Ancien membre de l'équipe", ""
    if a.is_candidate:
        return (a.candidate.full_name if a.candidate else a.display_name), "Étudiant(e)"
    link = zeus_service.class_link(a, klass)
    return a.display_name, (link.function if link else "Coach")


# ---- inboxes and unread

def _coach_class_ids(user) -> list[int]:
    """The classes a coach still teaches: his conversations there are his."""
    return [link.class_id for link in user.zeus_class_links if link.accepted_at]


def inbox_classes(user) -> list[ZeusClass]:
    """Choices of the class filter: the classes one teaches (reviewer: every class)."""
    stmt = select(ZeusClass).order_by(ZeusClass.cohort_id.desc(), ZeusClass.name)
    if not is_reviewer(user):
        stmt = stmt.where(ZeusClass.id.in_(_coach_class_ids(user)))
    return db.session.scalars(stmt).all()


def review_coaches() -> list[User]:
    """Choices of the coach filter of the review: the coaches who have conversations."""
    return db.session.scalars(select(User).where(User.id.in_(select(ZeusConversation.coach_id).distinct()))
                              .order_by(User.full_name, User.email)).all()


def inbox(user, class_id: int | None = None, awaiting: bool = False, page: int = 1, coach_id: int | None = None):
    """Coach: his own conversations, in the classes he still teaches. Reviewer: every conversation (filter by coach).
    Latest first, paginated."""
    stmt = select(ZeusConversation)
    if is_reviewer(user):
        if coach_id:
            stmt = stmt.where(ZeusConversation.coach_id == coach_id)
    else:
        stmt = stmt.where(ZeusConversation.coach_id == user.id, ZeusConversation.class_id.in_(_coach_class_ids(user)))
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
    coach: User
    function: str
    conversation: ZeusConversation | None
    last: ZeusMessage | None
    unread: bool
    writable: bool


@dataclass
class ClassGroup:
    klass: ZeusClass
    member: bool
    rows: list[StudentRow]


def student_inbox(enrollment: Enrollment, user) -> list[ClassGroup]:
    """One group per class of the student, then per former class whose conversations exist. In each: one row per
    coach of the team (managers first), then per former coach whose conversation exists (read-only)."""
    convs = list(db.session.scalars(select(ZeusConversation).where(ZeusConversation.enrollment_id == enrollment.id)))
    last, unread = last_messages(convs), unread_ids(user, convs)
    by_class: dict[int, dict[int, ZeusConversation]] = {}
    for c in convs:
        by_class.setdefault(c.class_id, {})[c.coach_id] = c
    classes = zeus_service.classes_for_student(enrollment)
    member_ids = {k.id for k in classes}
    former = {c.class_id: c.klass for c in convs if c.class_id not in member_ids}

    def row(coach, function, conv, writable):
        return StudentRow(coach, function, conv, last.get(conv.id) if conv else None, bool(conv and conv.id in unread),
                          writable)

    groups = []
    for k in classes + sorted(former.values(), key=lambda k: k.name):
        member, mine = k.id in member_ids, by_class.get(k.id, {})
        rows = [row(link.user, link.function, mine.get(link.user_id), member) for link in k.staff
                if member or link.user_id in mine]  # a former class: its conversations only
        staff_ids = {link.user_id for link in k.staff}
        rows += [row(c.coach, "Ancien coach de la classe", c, False) for coach_id, c in mine.items()
                 if coach_id not in staff_ids]
        groups.append(ClassGroup(k, member, rows))
    return groups


def unread_count(user) -> int:
    """The menu badge. Student: conversations with a message he has not seen. Coach: his conversations (in the
    classes he still teaches) where the student wrote last and he has not read it yet. The admin reviews, he owes no
    answer: none (unless he is himself a coach of a class)."""
    stmt = (select(func.count(ZeusConversation.id))
            .outerjoin(ZeusConversationRead, and_(ZeusConversationRead.conversation_id == ZeusConversation.id,
                                                  ZeusConversationRead.user_id == user.id))
            .where(or_(ZeusConversationRead.id.is_(None),
                       ZeusConversation.last_message_at > ZeusConversationRead.last_read_at)))
    e = zeus_service.student_enrollment(user)
    if e is not None:
        return db.session.scalar(stmt.where(ZeusConversation.enrollment_id == e.id))
    ids = _coach_class_ids(user)
    if not ids:
        return 0
    return db.session.scalar(stmt.where(ZeusConversation.coach_id == user.id, ZeusConversation.class_id.in_(ids),
                                        ZeusConversation.awaiting_reply.is_(True)))
