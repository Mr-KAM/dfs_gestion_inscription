"""Zeus mode: who may enter, classes and invitations, courses, scheduled sessions, roll calls (DFS matricule)."""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import or_, select

from app.extensions import db
from app.models import (Cohort, Enrollment, User, ZeusAttendance, ZeusClass, ZeusClassCoach, ZeusClassMember,
                        ZeusClassRole, ZeusCourse, ZeusNotification, ZeusSession)
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
    """Zeus users: admin, reception, residence, the coaches of a cohort or of a class (an invitation to answer
    counts), the registered students."""
    if user.role in DESK_ROLES:
        return True
    if user.role in COACH_ROLES:
        return bool(user.cohort_roles or user.zeus_class_links)
    return student_enrollment(user) is not None


def lands_in_zeus(user) -> bool:
    """At login, when the admin has switched the mode on."""
    return enabled() and can_enter(user)


def stays_in_zeus(user) -> bool:
    """One login, one interface: every Zeus user but the admin always comes back to Zeus (the admin, once at login)."""
    return user.role != ROLE_ADMIN and lands_in_zeus(user)


def cohorts_for(user) -> list[Cohort]:
    if user.role in DESK_ROLES:
        return db.session.scalars(select(Cohort).order_by(Cohort.created_at.desc())).all()
    if user.role in COACH_ROLES:  # the cohorts the admin gave him, and those of the classes he teaches in
        cohorts = [cc.cohort for cc in user.cohort_roles]
        cohorts += [link.klass.cohort for link in user.zeus_class_links if link.accepted_at]
        return list(dict.fromkeys(cohorts))
    e = student_enrollment(user)
    return [e.cohort] if e else []


def can_see(user, cohort: Cohort) -> bool:
    return cohort in cohorts_for(user)


def can_manage(user, cohort: Cohort) -> bool:
    """Cohort level (create classes, courses, any session): the admin, or a coach technique of the cohort."""
    return user.role == ROLE_ADMIN or (user.role == ROLE_COACH and any(cc.user_id == user.id for cc in cohort.coaches))


def class_link(user, klass: ZeusClass) -> ZeusClassCoach | None:
    """The user's accepted place in the class's teaching staff."""
    return next((c for c in klass.coaches if c.user_id == user.id and c.accepted_at), None)


def can_manage_class(user, klass: ZeusClass) -> bool:
    """A SAS coach's powers on the class: students, sessions, roll calls, notifications; invites coaches.
    The admin, or a manager of the class (named by the admin; the coach who created it)."""
    link = class_link(user, klass)
    return user.role == ROLE_ADMIN or bool(link and link.is_manager)


def can_teach_class(user, klass: ZeusClass) -> bool:
    """Any coach of the class (roll calls of its sessions, notifications to it), managers included."""
    return can_manage_class(user, klass) or class_link(user, klass) is not None


def can_schedule(user, cohort: Cohort, klass: ZeusClass | None = None) -> bool:
    """Programme a session: for the whole cohort (cohort level), or for a class one manages."""
    return can_manage(user, cohort) or (klass is not None and can_manage_class(user, klass))


def can_call(user, session: ZeusSession) -> bool:
    """Roll call: cohort level, the coach giving the session, or a coach of the session's class."""
    return (can_manage(user, session.course.cohort) or session.coach_id == user.id
            or (session.klass is not None and can_teach_class(user, session.klass)))


def cohort_coaches(cohort: Cohort) -> list[User]:
    """The cohort's coaches and its classes' coaches (course / session coach choices)."""
    users = [cc.user for cc in cohort.coaches]
    users += [c.user for k in classes_of(cohort) for c in k.staff]
    return list(dict.fromkeys(users))


# ---- classes and invitations

def create_class(cohort: Cohort, name: str, user) -> ZeusClass:
    name = name.strip()[:80]
    if not name:
        raise BusinessError("Indiquez le nom de la classe.")
    if any(k.name == name for k in classes_of(cohort)):
        raise BusinessError(f"La classe « {name} » existe déjà dans cette cohorte.")
    klass = ZeusClass(cohort=cohort, name=name)
    db.session.add(klass)
    if user.role in COACH_ROLES:  # the coach who creates it manages it
        db.session.add(ZeusClassCoach(klass=klass, user=user, is_manager=True, invited_by=user.id, accepted_at=utcnow()))
    db.session.flush()
    audit_service.log("zeus_update", klass, {"class": name, "cohort": cohort.name})
    db.session.commit()
    return klass


def update_class(klass: ZeusClass, form) -> None:
    """Admin: name and description."""
    name = form.get("name", "").strip()[:80]
    if not name:
        raise BusinessError("Indiquez le nom de la classe.")
    if any(k.name == name and k.id != klass.id for k in classes_of(klass.cohort)):
        raise BusinessError(f"La classe « {name} » existe déjà dans cette cohorte.")
    audit_service.log("zeus_update", klass, {"class_renamed": [klass.name, name]})
    klass.name, klass.description = name, form.get("description", "").strip()[:255]
    db.session.commit()


def delete_class(klass: ZeusClass) -> None:
    """Admin. Its sessions go with it; refused once a roll call was taken in one of them (attendance history)."""
    sessions = db.session.scalars(select(ZeusSession).where(ZeusSession.class_id == klass.id)).all()
    if any(s.marks for s in sessions):
        raise BusinessError(f"Suppression impossible : des appels ont été faits dans les séances de « {klass.name} ». "
                            "Retirez plutôt ses étudiants.")
    audit_service.log("zeus_update", klass.cohort, {"class_deleted": klass.name, "sessions": len(sessions)})
    db.session.delete(klass)
    db.session.commit()


# ---- teaching staff of a class, and the catalogue of class roles

def class_roles() -> list[ZeusClassRole]:
    return db.session.scalars(select(ZeusClassRole).order_by(ZeusClassRole.name)).all()


def save_class_role(form, role: ZeusClassRole | None = None) -> ZeusClassRole:
    name = form.get("name", "").strip()[:80]
    if not name:
        raise BusinessError("Indiquez le nom du rôle.")
    if db.session.scalar(select(ZeusClassRole.id).where(ZeusClassRole.name == name,
                                                        ZeusClassRole.id != (role.id if role else 0))):
        raise BusinessError(f"Le rôle « {name} » existe déjà.")
    role = role or ZeusClassRole()
    role.name, role.description = name, form.get("description", "").strip()[:255]
    db.session.add(role)
    db.session.flush()
    audit_service.log("zeus_update", role, {"class_role": name})
    db.session.commit()
    return role


def delete_class_role(role: ZeusClassRole) -> None:
    """The coaches holding it keep their place in the class, without function (FK SET NULL)."""
    audit_service.log("zeus_update", details={"class_role_deleted": role.name})
    db.session.delete(role)
    db.session.commit()


def coach_choices(klass: ZeusClass) -> list[User]:
    """Active coaches (technique or métier) not yet in the class's staff or invited to it."""
    taken = {c.user_id for c in klass.coaches}
    return [u for u in db.session.scalars(select(User).where(User.role.in_(COACH_ROLES), User.active)
                                          .order_by(User.full_name)) if u.id not in taken]


def _class_role(role_id) -> ZeusClassRole | None:
    if not role_id:
        return None
    role = db.session.get(ZeusClassRole, role_id)
    if role is None:
        raise BusinessError("Rôle de classe inconnu.")
    return role


def add_coach(klass: ZeusClass, form, user) -> ZeusClassCoach:
    """Admin: added at once (no invitation), possibly as manager. A class manager: invites (he answers in Zeus),
    never as manager."""
    coach = next((u for u in coach_choices(klass) if u.id == form.get("user_id", type=int)), None)
    if coach is None:
        raise BusinessError("Choisissez un coach actif qui n'est pas encore dans la classe.")
    by_admin = user.role == ROLE_ADMIN
    link = ZeusClassCoach(klass=klass, user=coach, role=_class_role(form.get("role_id", type=int)),
                          is_manager=by_admin and form.get("is_manager") == "1", invited_by=user.id,
                          accepted_at=utcnow() if by_admin else None)
    db.session.add(link)
    audit_service.log("zeus_update", klass, {"coach_added" if by_admin else "coach_invited": coach.email,
                                             "function": link.function, "manager": link.is_manager})
    db.session.commit()
    return link


def update_coach(link: ZeusClassCoach, form, user) -> None:
    """Function (admin, or a manager for a non-manager coach); manager or not: admin only."""
    link.role = _class_role(form.get("role_id", type=int))
    if user.role == ROLE_ADMIN:
        link.is_manager = form.get("is_manager") == "1"
    audit_service.log("zeus_update", link.klass, {"coach": link.user.email, "function": link.function,
                                                  "manager": link.is_manager})
    db.session.commit()


def remove_coach(link: ZeusClassCoach) -> None:
    audit_service.log("zeus_update", link.klass, {"coach_removed": link.user.email})
    db.session.delete(link)
    db.session.commit()


def coach_invitations_for(user) -> list[ZeusClassCoach]:
    return [link for link in user.zeus_class_links if not link.accepted_at]


def answer_coach(link: ZeusClassCoach, accept: bool) -> None:
    audit_service.log("zeus_update", link.klass, {"coach_answer": "accepted" if accept else "declined",
                                                  "coach": link.user.email})
    if accept:
        link.accepted_at = utcnow()
    else:
        db.session.delete(link)
    db.session.commit()


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


# ---- notifications

def can_notify(user, cohort: Cohort) -> bool:
    """Admin, reception, or a coach (technique or métier) the admin gave access to the cohort."""
    return user.role in (ROLE_ADMIN, ROLE_RECEPTION) or any(cc.user_id == user.id for cc in cohort.coaches)


def notification_targets(user) -> list[tuple[Cohort, list[ZeusClass], bool]]:
    """(cohort, classes he may write to, whole cohort and its students too?) for the send form."""
    targets = []
    for cohort in cohorts_for(user):
        full = can_notify(user, cohort)
        classes = [k for k in classes_of(cohort) if full or can_teach_class(user, k)]
        if full or classes:
            targets.append((cohort, classes, full))
    return targets


def send_notification(cohort: Cohort, target: str, form, user) -> ZeusNotification:
    """target: "cohort", "class:<id>" (a class of the cohort) or "student:<id>" (a registered student of it)."""
    title = form.get("title", "").strip()[:150]
    if not title:
        raise BusinessError("Le titre est obligatoire.")
    kind, _, target_id = target.partition(":")
    n = ZeusNotification(cohort=cohort, title=title, body=form.get("body", "").strip()[:5000], created_by=user.id)
    if kind == "class":
        n.klass = next((k for k in classes_of(cohort) if str(k.id) == target_id), None)
        if n.klass is None:
            raise BusinessError("Choisissez une classe de la cohorte.")
    elif kind == "student":
        n.enrollment = next((e for e in cohort.enrollments if e.matricule and str(e.id) == target_id), None)
        if n.enrollment is None:
            raise BusinessError("Choisissez un étudiant inscrit de la cohorte.")
    if not (can_notify(user, cohort) or (n.klass is not None and can_teach_class(user, n.klass))):
        raise BusinessError("Vous ne pouvez écrire qu'aux classes dont vous êtes coach.")
    db.session.add(n)
    db.session.flush()
    audit_service.log("zeus_update", n, {"notification": title, "to": n.target_label})
    db.session.commit()
    return n


def notifications_for(user, limit: int | None = None) -> list[ZeusNotification]:
    """Student: his cohort's, his classes' and his own. Staff: everything sent in their cohorts."""
    cohorts = cohorts_for(user)
    if not cohorts:
        return []
    stmt = select(ZeusNotification).where(ZeusNotification.cohort_id.in_([c.id for c in cohorts]))
    e = student_enrollment(user)
    if e:
        stmt = stmt.where(or_(
            ZeusNotification.enrollment_id == e.id,
            ZeusNotification.enrollment_id.is_(None) & (ZeusNotification.class_id.is_(None) | ZeusNotification.class_id
                                                        .in_([k.id for k in classes_for_student(e)]))))
    stmt = stmt.order_by(ZeusNotification.created_at.desc(), ZeusNotification.id.desc())
    return db.session.scalars(stmt.limit(limit) if limit else stmt).all()


def delete_notification(n: ZeusNotification) -> None:
    audit_service.log("zeus_update", n.cohort, {"notification_deleted": n.title})
    db.session.delete(n)
    db.session.commit()


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
