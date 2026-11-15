"""Direction (DG, DGA): statistics of the selection juries and of the cohorts; the activities calendar."""
from collections import Counter
from datetime import datetime, timezone

from sqlalchemy import select

from app.extensions import db
from app.models import Activity, Cohort, Defense, User
from app.models.defense import DECISIONS
from app.models.user import PLANNER_ROLES, ROLE_CANDIDATE, ROLE_LABELS
from app.services import audit_service
from app.services.campus_service import local_datetime
from app.services.candidate_service import BusinessError


def defense_stats() -> list[dict]:
    """One row per SAS defense (selection jury): juries, students, decisions."""
    rows = []
    for d in db.session.scalars(select(Defense).order_by(Defense.id.desc())):
        students = [jc for jury in d.juries for jc in jury.candidates]
        decisions = Counter((jc.sheet or {}).get("decision") for jc in students)
        rows.append({"defense": d, "juries": len(d.juries), "validated": sum(1 for j in d.juries if j.validated_at),
                     "students": len(students), "decided": sum(decisions[k] for k in DECISIONS),
                     "decisions": {k: decisions[k] for k in DECISIONS}})
    return rows


def cohort_stats() -> list[dict]:
    rows = []
    for c in db.session.scalars(select(Cohort).order_by(Cohort.created_at.desc())):
        genders = Counter(e.candidate.gender for e in c.enrollments)
        rows.append({"cohort": c, "eligible": len(c.enrollments),
                     "registered": sum(1 for e in c.enrollments if e.matricule),
                     "women": genders["F"], "men": genders["M"]})
    return rows


# ---- activities calendar

def can_plan(user) -> bool:
    return user.role in PLANNER_ROLES


def audience_choices() -> tuple[dict, list[User]]:
    """Roles (students included) and the active accounts one can name."""
    people = db.session.scalars(select(User).where(User.active.is_(True))
                                .order_by(User.role, User.full_name, User.email)).all()
    return ROLE_LABELS, people


def create(form, user) -> Activity:
    title = form.get("title", "").strip()[:150]
    starts, ends = local_datetime(form.get("starts_at", "")), local_datetime(form.get("ends_at", ""))
    if not title:
        raise BusinessError("Le titre est obligatoire.")
    if not starts or not ends or ends <= starts:
        raise BusinessError("Indiquez le début et la fin de l'activité (la fin après le début).")
    roles = [r for r in form.getlist("roles") if r in ROLE_LABELS]
    ids = form.getlist("user_ids", type=int)
    people = db.session.scalars(select(User).where(User.id.in_(ids))).all() if ids else []
    everyone = form.get("everyone") == "1"
    if not (everyone or roles or people):
        raise BusinessError("Désignez qui est concerné : tout le monde, des profils ou des personnes.")
    a = Activity(title=title, description=form.get("description", "").strip()[:5000],
                 place=form.get("place", "").strip()[:150], starts_at=starts, ends_at=ends, everyone=everyone,
                 roles=roles, participants=people, created_by=user.id)
    db.session.add(a)
    db.session.flush()
    audit_service.log("activity_update", a, {"title": title, "starts_at": starts.isoformat()})
    db.session.commit()
    return a


def delete(a: Activity) -> None:
    audit_service.log("activity_update", a, {"deleted": a.title})
    db.session.delete(a)
    db.session.commit()


def activities_for(user, start: datetime | None = None, end: datetime | None = None,
                   limit: int | None = None) -> list[Activity]:
    """The activities of [start, end) concerning the user; the planners see them all."""
    stmt = select(Activity).order_by(Activity.starts_at)
    if start:
        stmt = stmt.where(Activity.ends_at > start.astimezone(timezone.utc))
    if end:
        stmt = stmt.where(Activity.starts_at < end.astimezone(timezone.utc))
    # ponytail: filtered in Python (a few activities a week); a SQL filter when they number in thousands.
    rows = [a for a in db.session.scalars(stmt) if can_plan(user) or a.concerns(user)]
    return rows[:limit] if limit else rows


def audience_label(a: Activity) -> str:
    if a.everyone:
        return "Tout le monde"
    parts = [("Étudiants" if r == ROLE_CANDIDATE else ROLE_LABELS[r]) for r in a.roles if r in ROLE_LABELS]
    parts += [u.display_name for u in a.participants]
    return ", ".join(parts)
