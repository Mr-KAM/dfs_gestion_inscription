"""Coaches' teaching: coach métier invitations into a class, and the program calendar with its follow-up."""
import calendar
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.extensions import db
from app.models import CoachInvitation, Lesson, SasClass, User
from app.models.attendance import HOURS_CHOICES
from app.models.campus import INVITE_ACCEPTED, INVITE_DECLINED, INVITE_PENDING, LESSON_DONE, LESSON_STATUSES
from app.models.user import COACH_ROLES, ROLE_ADMIN, ROLE_COACH, ROLE_TRADE_COACH
from app.services import attendance_service, audit_service, settings_service
from app.services.campus_service import can_manage, local_datetime, parse_target
from app.services.candidate_service import BusinessError
from app.utils import as_utc, utcnow


# ---- coach métier invitations

def _may_invite(user, klass: SasClass) -> None:
    if user.role not in (ROLE_ADMIN, ROLE_COACH) or not can_manage(user, klass.sas_id, klass.name):
        raise BusinessError("Seuls les coachs techniques de la classe et l'admin invitent un coach métier.")


def trade_coaches_for(klass: SasClass) -> list[User]:
    """Active coaches métier not yet teaching in this class."""
    return [u for u in db.session.scalars(select(User).where(User.role == ROLE_TRADE_COACH, User.active.is_(True))
                                          .order_by(User.full_name, User.email))
            if klass.key not in (u.coach_classes or [])]


def invite_coach(klass: SasClass, user, coach_id: int) -> CoachInvitation:
    _may_invite(user, klass)
    coach = db.session.get(User, coach_id or 0)
    if coach is None or coach.role != ROLE_TRADE_COACH or not coach.active:
        raise BusinessError("Choisissez un coach métier.")
    if klass.key in (coach.coach_classes or []):
        raise BusinessError(f"{coach.display_name} intervient déjà dans cette classe.")
    invitation = db.session.scalar(select(CoachInvitation).where(CoachInvitation.class_id == klass.id,
                                                                 CoachInvitation.user_id == coach.id))
    if invitation is None:  # a declined invitation is sent again
        invitation = CoachInvitation(klass=klass, user_id=coach.id)
        db.session.add(invitation)
    invitation.status, invitation.invited_by, invitation.created_at = INVITE_PENDING, user.id, utcnow()
    invitation.responded_at = None
    audit_service.log("coach_invite", klass, {"coach": coach.email})
    db.session.commit()
    return invitation


def class_coach_invitations(klass: SasClass) -> list[CoachInvitation]:
    return db.session.scalars(select(CoachInvitation).where(CoachInvitation.class_id == klass.id)
                              .order_by(CoachInvitation.created_at.desc())).all()


def pending_coach_invitations(user) -> list[CoachInvitation]:
    return db.session.scalars(select(CoachInvitation).where(CoachInvitation.user_id == user.id,
                                                            CoachInvitation.status == INVITE_PENDING)
                              .order_by(CoachInvitation.created_at.desc())).all()


def answer_coach_invitation(invitation: CoachInvitation, user, accept: bool) -> None:
    if invitation.user_id != user.id or invitation.status != INVITE_PENDING:
        raise BusinessError("Invitation introuvable ou déjà traitée.")
    invitation.status, invitation.responded_at = (INVITE_ACCEPTED if accept else INVITE_DECLINED), utcnow()
    if accept:  # new list: JSON change tracked
        user.coach_classes = sorted({*(user.coach_classes or []), invitation.klass.key})
    audit_service.log("coach_invite_answer", invitation.klass, {"coach": user.email, "accepted": accept})
    db.session.commit()


def remove_coach(klass: SasClass, user, coach_id: int) -> User:
    """A coach technique of the class (or the admin) withdraws a coach métier from it."""
    _may_invite(user, klass)
    coach = db.session.get(User, coach_id or 0)
    if coach is None or coach.role != ROLE_TRADE_COACH:
        raise BusinessError("Seul un coach métier peut être retiré ici (les coachs techniques : par l'admin).")
    coach.coach_classes = [k for k in coach.coach_classes or [] if k != klass.key]
    audit_service.log("coach_remove", klass, {"coach": coach.email})
    db.session.commit()
    return coach


# ---- program calendar (lessons) and follow-up

def _zone() -> ZoneInfo:
    return ZoneInfo(settings_service.get("timezone") or "UTC")


def local_day(value) -> date:
    return as_utc(value).astimezone(_zone()).date()


def can_teach(user, lesson: Lesson) -> bool:
    """Admin, or a coach (technique / métier) of the lesson's class."""
    return user.role == ROLE_ADMIN or (user.role in COACH_ROLES and can_manage(user, lesson.sas_id, lesson.class_name))


def save_lesson(user, form, lesson: Lesson | None = None) -> Lesson:
    """Create (target = a class the user teaches) or update a lesson, follow-up included (status, report)."""
    if lesson is None:  # added to the session only once valid (lookups below autoflush)
        sas, class_name = parse_target(user, form.get("target", ""))
        if not class_name:
            raise BusinessError("Choisissez une classe.")
        lesson = Lesson(sas_id=sas.id, class_name=class_name, created_by=user.id)
    title = " ".join(form.get("title", "").split())[:150]
    if not title:
        raise BusinessError("Donnez un intitulé au cours.")
    starts_at = local_datetime(form.get("starts_at", ""))
    if starts_at is None:
        raise BusinessError("Indiquez la date et l'heure du cours.")
    duration = form.get("duration", type=int)
    if duration is None or not 15 <= duration <= 600:
        raise BusinessError("Durée du cours : entre 15 et 600 minutes.")
    coach = db.session.get(User, form.get("coach_id", type=int) or 0)
    if coach is None and user.role in COACH_ROLES:
        coach = user
    if coach is not None and not (coach.role in COACH_ROLES and can_manage(coach, lesson.sas_id, lesson.class_name)):
        raise BusinessError(f"{coach.display_name} n'intervient pas dans la classe {lesson.class_name}.")
    lesson.title, lesson.starts_at, lesson.duration = title, starts_at, duration
    lesson.coach_id = coach.id if coach else None
    lesson.module = " ".join(form.get("module", "").split())[:150]
    lesson.objectives = form.get("objectives", "").strip()[:3000]
    if "status" in form:
        lesson.status = form["status"] if form["status"] in LESSON_STATUSES else lesson.status
        lesson.report = form.get("report", "").strip()[:5000]
    db.session.add(lesson)
    db.session.flush()
    audit_service.log("lesson_update", lesson, {"class": lesson.class_name, "title": title, "status": lesson.status})
    db.session.commit()
    return lesson


def repeat_lesson(lesson: Lesson, weeks: int) -> int:
    """Copies of a new lesson on the following weeks (same day and time): a recurring course in one go."""
    weeks = max(0, min(weeks or 0, 20))
    for n in range(1, weeks + 1):
        db.session.add(Lesson(sas_id=lesson.sas_id, class_name=lesson.class_name, coach_id=lesson.coach_id,
                              starts_at=as_utc(lesson.starts_at) + timedelta(weeks=n), duration=lesson.duration,
                              title=lesson.title, module=lesson.module, objectives=lesson.objectives,
                              created_by=lesson.created_by))
    db.session.commit()
    return weeks


def class_coaches(lesson_or_key) -> list[User]:
    """Coaches who may lead lessons of a class ('sas_id:class' key)."""
    key = lesson_or_key if isinstance(lesson_or_key, str) else f"{lesson_or_key.sas_id}:{lesson_or_key.class_name}"
    return [u for u in db.session.scalars(select(User).where(User.role.in_(COACH_ROLES), User.active.is_(True))
                                          .order_by(User.full_name, User.email)) if key in (u.coach_classes or [])]


def all_coaches() -> list[User]:
    return db.session.scalars(select(User).where(User.role.in_(COACH_ROLES), User.active.is_(True))
                              .order_by(User.full_name, User.email)).all()


def month_bounds(month: str) -> tuple[date, datetime, datetime]:
    """'YYYY-MM' (this month if invalid) -> (first day, UTC start, UTC end) in the school's time zone."""
    try:
        first = datetime.strptime(month, "%Y-%m").date().replace(day=1)
    except (TypeError, ValueError):
        first = utcnow().astimezone(_zone()).date().replace(day=1)
    following = (first + timedelta(days=32)).replace(day=1)
    zone = _zone()
    return (first, datetime.combine(first, datetime.min.time(), zone),
            datetime.combine(following, datetime.min.time(), zone))


def month_view(lessons: list[Lesson], first: date) -> dict:
    """Weeks (Monday first) of the month, lessons by local day, and the follow-up figures."""
    by_day: dict[date, list[Lesson]] = {}
    for lesson in lessons:
        by_day.setdefault(local_day(lesson.starts_at), []).append(lesson)
    counts = {status: sum(1 for x in lessons if x.status == status) for status in LESSON_STATUSES}
    return {"weeks": calendar.Calendar(firstweekday=0).monthdatescalendar(first.year, first.month),
            "by_day": by_day, "counts": counts,
            "hours_planned": round(sum(x.duration for x in lessons) / 60, 1),
            "hours_done": round(sum(x.duration for x in lessons if x.status == LESSON_DONE) / 60, 1),
            "prev": (first - timedelta(days=1)).strftime("%Y-%m"),
            "next": (first + timedelta(days=32)).strftime("%Y-%m"), "today": utcnow().astimezone(_zone()).date()}


def open_roll_call(lesson: Lesson, user, minutes: int, hours: int):
    """Roll call for this lesson (its class, title and start time); reused if already opened."""
    if lesson.attendance_session is not None:
        return lesson.attendance_session
    start = max(as_utc(lesson.starts_at), utcnow())  # a past lesson: the roll call opens now
    session = attendance_service.open_session(lesson.sas, lesson.class_name, lesson.title, minutes, user,
                                              starts_at=start, hours=hours)
    lesson.attendance_session_id = session.id
    db.session.commit()
    return session


def default_hours(lesson: Lesson) -> int:
    return min(HOURS_CHOICES, key=lambda h: abs(h * 60 - lesson.duration))
