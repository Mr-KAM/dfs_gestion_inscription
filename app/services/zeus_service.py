"""Zeus mode: who may enter, classes and invitations, courses, scheduled sessions, roll calls (DFS matricule)."""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.extensions import db
from app.models import (Cohort, Enrollment, User, ZeusAttendance, ZeusClass, ZeusClassMember, ZeusCourse,
                        ZeusSession)
from app.models.user import COACH_ROLES, ROLE_ADMIN, ROLE_COACH, ROLE_RECEPTION, ROLE_RESIDENCE
from app.models.zeus import METHOD_MANUAL, METHOD_SCAN
from app.services import audit_service, settings_service
from app.services.campus_service import local_datetime
from app.services.candidate_service import BusinessError, decode_matricule
from app.utils import utcnow

DESK_ROLES = (ROLE_ADMIN, ROLE_RECEPTION, ROLE_RESIDENCE)  # every cohort, read (desk: registrations)


# ---- mode and access

def enabled() -> bool:
    return settings_service.get("zeus_mode") == "1"


def set_enabled(on: bool) -> None:
    settings_service.set_many({"zeus_mode": "1" if on else ""})
    audit_service.log("zeus_mode", details={"enabled": on})
    db.session.commit()


def student_enrollment(user) -> Enrollment | None:
    """The registered (DFS matricule) enrollment of a candidate account, else None."""
    e = user.candidate.enrollment if user.is_candidate and user.candidate else None
    return e if e and e.matricule else None


def can_enter(user) -> bool:
    """Zeus users: admin, reception, residence, the coaches of a cohort, the registered students."""
    if user.role in DESK_ROLES:
        return True
    if user.role in COACH_ROLES:
        return bool(user.cohort_roles)
    return student_enrollment(user) is not None


def lands_in_zeus(user) -> bool:
    """At login, when the admin has switched the mode on."""
    return enabled() and can_enter(user)


def cohorts_for(user) -> list[Cohort]:
    if user.role in DESK_ROLES:
        return db.session.scalars(select(Cohort).order_by(Cohort.created_at.desc())).all()
    if user.role in COACH_ROLES:
        return [cc.cohort for cc in user.cohort_roles]
    e = student_enrollment(user)
    return [e.cohort] if e else []


def can_see(user, cohort: Cohort) -> bool:
    return cohort in cohorts_for(user)


def can_manage(user, cohort: Cohort) -> bool:
    """Classes, courses, sessions: the admin, or a coach technique the admin gave access to the cohort."""
    return user.role == ROLE_ADMIN or (user.role == ROLE_COACH and any(cc.user_id == user.id for cc in cohort.coaches))


def can_call(user, session: ZeusSession) -> bool:
    """Roll call: whoever manages the cohort, or the coach (technique or métier) giving the session."""
    return can_manage(user, session.course.cohort) or session.coach_id == user.id


def cohort_coaches(cohort: Cohort) -> list[User]:
    return [cc.user for cc in cohort.coaches]


# ---- classes and invitations

def create_class(cohort: Cohort, name: str, user) -> ZeusClass:
    name = name.strip()[:80]
    if not name:
        raise BusinessError("Indiquez le nom de la classe.")
    if any(k.name == name for k in classes_of(cohort)):
        raise BusinessError(f"La classe « {name} » existe déjà dans cette cohorte.")
    klass = ZeusClass(cohort=cohort, name=name, coach_id=user.id if user.role in COACH_ROLES else None)
    db.session.add(klass)
    db.session.flush()
    audit_service.log("zeus_update", klass, {"class": name, "cohort": cohort.name})
    db.session.commit()
    return klass


def classes_of(cohort: Cohort) -> list[ZeusClass]:
    return db.session.scalars(select(ZeusClass).where(ZeusClass.cohort_id == cohort.id).order_by(ZeusClass.name)).all()


def invitable(klass: ZeusClass) -> list[Enrollment]:
    """Registered students of the cohort, not yet invited to / member of the class."""
    taken = {m.enrollment_id for m in klass.members}
    return sorted((e for e in klass.cohort.enrollments if e.matricule and e.id not in taken),
                  key=lambda e: e.candidate.full_name)


def invite(klass: ZeusClass, enrollment_ids: list[int], user) -> int:
    """Only ids from invitable(): a forged form cannot invite a student of another cohort."""
    chosen = [e for e in invitable(klass) if e.id in set(enrollment_ids)]
    for e in chosen:
        db.session.add(ZeusClassMember(klass=klass, enrollment=e, invited_by=user.id))
    audit_service.log("zeus_update", klass, {"invited": [e.matricule for e in chosen]})
    db.session.commit()
    return len(chosen)


def invitations_for(enrollment: Enrollment) -> list[ZeusClassMember]:
    return db.session.scalars(select(ZeusClassMember).where(ZeusClassMember.enrollment_id == enrollment.id,
                                                            ZeusClassMember.joined_at.is_(None))).all()


def classes_for_student(enrollment: Enrollment) -> list[ZeusClass]:
    return [m.klass for m in db.session.scalars(select(ZeusClassMember).where(
        ZeusClassMember.enrollment_id == enrollment.id, ZeusClassMember.joined_at.is_not(None)))]


def answer(member: ZeusClassMember, accept: bool) -> None:
    audit_service.log("zeus_update", member.klass, {"answer": "accepted" if accept else "declined",
                                                    "student": member.enrollment.matricule})
    if accept:
        member.joined_at = utcnow()
    else:
        db.session.delete(member)
    db.session.commit()


def remove_member(member: ZeusClassMember) -> None:
    audit_service.log("zeus_update", member.klass, {"removed": member.enrollment.matricule})
    db.session.delete(member)
    db.session.commit()


# ---- courses and sessions (calendar)

def _coach_id(cohort: Cohort, form) -> int | None:
    coach_id = form.get("coach_id", type=int)
    if coach_id and coach_id not in {u.id for u in cohort_coaches(cohort)}:
        raise BusinessError("Choisissez un coach de la cohorte.")
    return coach_id


def save_course(cohort: Cohort, form, course: ZeusCourse | None = None) -> ZeusCourse:
    name = form.get("name", "").strip()[:150]
    if not name:
        raise BusinessError("Indiquez le nom du cours.")
    if db.session.scalar(select(ZeusCourse.id).where(ZeusCourse.cohort_id == cohort.id, ZeusCourse.name == name,
                                                     ZeusCourse.id != (course.id if course else 0))):
        raise BusinessError(f"Le cours « {name} » existe déjà dans cette cohorte.")
    course = course or ZeusCourse(cohort=cohort)
    course.name, course.description = name, form.get("description", "").strip()[:255]
    course.hours = max(0, min(form.get("hours", 0, type=int) or 0, 2000))
    course.coach_id = _coach_id(cohort, form)
    db.session.add(course)
    db.session.flush()
    audit_service.log("zeus_update", course, {"course": name, "hours": course.hours})
    db.session.commit()
    return course


def courses_of(cohort: Cohort) -> list[ZeusCourse]:
    return db.session.scalars(select(ZeusCourse).where(ZeusCourse.cohort_id == cohort.id)
                              .order_by(ZeusCourse.name)).all()


def create_session(cohort: Cohort, form, user) -> ZeusSession:
    course = db.session.get(ZeusCourse, form.get("course_id", 0, type=int))
    if course is None or course.cohort_id != cohort.id:
        raise BusinessError("Choisissez un cours de la cohorte.")
    class_id = form.get("class_id", type=int)
    if class_id and class_id not in {k.id for k in classes_of(cohort)}:
        raise BusinessError("Choisissez une classe de la cohorte.")
    starts, ends = local_datetime(form.get("starts_at", "")), local_datetime(form.get("ends_at", ""))
    if not starts or not ends or ends <= starts:
        raise BusinessError("Indiquez le début et la fin de la séance (la fin après le début).")
    session = ZeusSession(course=course, class_id=class_id or None, starts_at=starts, ends_at=ends,
                          room=form.get("room", "").strip()[:100],
                          coach_id=_coach_id(cohort, form) or course.coach_id, created_by=user.id)
    db.session.add(session)
    db.session.flush()
    audit_service.log("zeus_update", session, {"course": course.name, "starts_at": starts.isoformat()})
    db.session.commit()
    return session


def delete_session(session: ZeusSession) -> None:
    audit_service.log("zeus_update", session.course, {"session_deleted": session.starts_at.isoformat()})
    db.session.delete(session)
    db.session.commit()


def week_start(value: str | None) -> datetime:
    """Monday 00:00 (school time zone) of the week of `value` (YYYY-MM-DD), this week by default."""
    tz = ZoneInfo(settings_service.get("timezone") or "UTC")
    try:
        day = datetime.fromisoformat(value).date() if value else datetime.now(tz).date()
    except ValueError:
        day = datetime.now(tz).date()
    day -= timedelta(days=day.weekday())
    return datetime(day.year, day.month, day.day, tzinfo=tz)


def sessions_for(user, start: datetime, end: datetime, cohort: Cohort | None = None) -> list[ZeusSession]:
    """The sessions of [start, end) the user may see: his cohorts; a student, those of his classes or the cohort."""
    cohorts = [c for c in cohorts_for(user) if cohort is None or c.id == cohort.id]
    if not cohorts:
        return []
    rows = db.session.scalars(select(ZeusSession).join(ZeusCourse).where(
        ZeusCourse.cohort_id.in_([c.id for c in cohorts]),
        ZeusSession.starts_at >= start.astimezone(timezone.utc), ZeusSession.starts_at < end.astimezone(timezone.utc))
        .order_by(ZeusSession.starts_at)).all()
    e = student_enrollment(user)
    if e:
        mine = {k.id for k in classes_for_student(e)}
        rows = [s for s in rows if s.class_id is None or s.class_id in mine]
    return rows


# ---- roll calls

def save_roll_call(session: ZeusSession, present_ids: set[int], user) -> int:
    """Manual roll call: ticked = present, unticked = absent (its mark is removed). Returns the present count."""
    expected = {e.id: e for e in session.expected}
    marks = {m.enrollment_id: m for m in session.marks}
    for eid, mark in marks.items():
        if eid in expected and eid not in present_ids:
            db.session.delete(mark)
    for eid in present_ids & expected.keys() - marks.keys():
        db.session.add(ZeusAttendance(session=session, enrollment=expected[eid], method=METHOD_MANUAL,
                                      marked_by=user.id))
    present = len(present_ids & expected.keys())
    audit_service.log("zeus_attendance", session, {"present": present, "expected": len(expected)})
    db.session.commit()
    return present


def scan(session: ZeusSession, token: str, user) -> tuple[Enrollment, bool]:
    """The student's DFS QR code (Zeus space) scanned by the coach: (enrollment, newly marked)."""
    matricule = decode_matricule(token)
    e = next((e for e in session.expected if e.matricule == matricule), None)
    if e is None:
        raise BusinessError(f"{matricule} : étudiant non attendu à cette séance (autre classe ou cohorte).")
    if any(m.enrollment_id == e.id for m in session.marks):
        return e, False
    db.session.add(ZeusAttendance(session=session, enrollment=e, method=METHOD_SCAN, marked_by=user.id))
    audit_service.log("zeus_attendance", session, {"scanned": matricule})
    db.session.commit()
    return e, True


def student_attendance(enrollment: Enrollment, user) -> list[tuple[ZeusSession, bool]]:
    """The student's past sessions, most recent first, with present / absent."""
    sessions = [s for s in sessions_for(user, datetime(2000, 1, 1, tzinfo=timezone.utc), utcnow()) if s.is_past]
    present = {m.session_id for m in db.session.scalars(select(ZeusAttendance).where(
        ZeusAttendance.enrollment_id == enrollment.id))}
    return [(s, s.id in present) for s in reversed(sessions)]
