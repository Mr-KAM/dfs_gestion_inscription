"""SAS campus: who manages which class, notifications, exercises, visits, incidents."""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import func, or_, select

from app.extensions import db
from app.models import Candidate, Exercise, ExerciseSubmission, Incident, Notification, Sas, Visit
from app.models.campus import INCIDENT_CATEGORIES, REJECTED, SUBMITTED, VALIDATED
from app.models.user import ROLE_ADMIN, ROLE_COACH, ROLE_RECEPTION
from app.services import audit_service, settings_service
from app.services.candidate_service import BusinessError
from app.utils import utcnow


# ---- classes and rights

def class_choices() -> list[dict]:
    """Every (SAS, class) holding students: [{key, sas, class_name, count}]."""
    rows = db.session.execute(
        select(Sas, Candidate.sas_class, func.count(Candidate.id)).join(Candidate, Candidate.sas_id == Sas.id)
        .where(Candidate.sas_class.is_not(None), Candidate.sas_class != "")
        .group_by(Sas.id, Candidate.sas_class).order_by(Sas.name, Candidate.sas_class)).all()
    return [{"key": f"{sas.id}:{name}", "sas": sas, "class_name": name, "count": count} for sas, name, count in rows]


def can_manage(user, sas_id: int, class_name: str) -> bool:
    """Admin and reception: everything. Coach: his own classes (not a whole SAS)."""
    if user.role in (ROLE_ADMIN, ROLE_RECEPTION):
        return True
    return user.role == ROLE_COACH and bool(class_name) and f"{sas_id}:{class_name}" in (user.coach_classes or [])


def targets(user) -> list[dict]:
    """Classes (and whole SAS for admin / reception) the user may address: [{key, label}]."""
    out = []
    if user.role in (ROLE_ADMIN, ROLE_RECEPTION):
        out = [{"key": f"{s.id}:", "label": f"{s.name} — tout le SAS"} for s in db.session.scalars(select(Sas).order_by(Sas.name))]
    out += [{"key": c["key"], "label": f"{c['sas'].name} — {c['class_name']} ({c['count']})"}
            for c in class_choices() if can_manage(user, c["sas"].id, c["class_name"])]
    return out


def parse_target(user, key: str) -> tuple[Sas, str]:
    sas_id, _, class_name = (key or "").partition(":")
    sas = db.session.get(Sas, int(sas_id)) if sas_id.isdigit() else None
    if sas is None or not can_manage(user, sas.id, class_name):
        raise BusinessError("Classe non autorisée.")
    return sas, class_name


def _for_candidate(model, candidate: Candidate):
    """Rows of `model` addressed to this student (his class or his whole SAS)."""
    return select(model).where(model.sas_id == candidate.sas_id,
                               or_(model.class_name == "", model.class_name == (candidate.sas_class or "")))


def visible_query(model, user):
    """Notifications / exercises the staff member may see: all for admin & reception, own classes for a coach."""
    stmt = select(model)
    if user.role not in (ROLE_ADMIN, ROLE_RECEPTION, ROLE_COACH):
        return stmt.where(model.id < 0)
    if user.role == ROLE_COACH:
        keys = user.coach_classes or []
        stmt = stmt.where(or_(*[(model.sas_id == int(k.split(":", 1)[0])) & (model.class_name == k.split(":", 1)[1])
                                for k in keys])) if keys else stmt.where(model.id < 0)
    return stmt


def local_datetime(value: str) -> datetime | None:
    """<input type="datetime-local"> is in the school's time zone; stored in UTC."""
    if not value:
        return None
    try:
        local = datetime.fromisoformat(value)
    except ValueError:
        raise BusinessError("Date invalide.")
    tz = ZoneInfo(settings_service.get("timezone") or "UTC")
    return local.replace(tzinfo=tz).astimezone(timezone.utc)


# ---- notifications

def send_notification(user, target: str, title: str, body: str) -> Notification:
    sas, class_name = parse_target(user, target)
    title = title.strip()[:150]
    if not title:
        raise BusinessError("Le titre est obligatoire.")
    notification = Notification(sas=sas, class_name=class_name, title=title, body=body.strip()[:5000], created_by=user.id)
    db.session.add(notification)
    db.session.flush()
    audit_service.log("notification_send", notification, {"class": class_name, "title": title})
    db.session.commit()
    return notification


def notifications_for(candidate: Candidate, limit: int | None = None) -> list[Notification]:
    if not candidate.sas_id:
        return []
    stmt = _for_candidate(Notification, candidate).order_by(Notification.created_at.desc())
    return db.session.scalars(stmt.limit(limit) if limit else stmt).all()


# ---- exercises

def create_exercise(user, form) -> Exercise:
    sas, class_name = parse_target(user, form.get("target", ""))
    title = form.get("title", "").strip()[:150]
    if not title:
        raise BusinessError("Le titre est obligatoire.")
    opens_at = local_datetime(form.get("opens_at", "")) or utcnow()
    due_at = local_datetime(form.get("due_at", ""))
    if due_at and due_at <= opens_at:
        raise BusinessError("La date limite doit être après la publication.")
    exercise = Exercise(sas=sas, class_name=class_name, title=title, instructions=form.get("instructions", "").strip()[:20000],
                        opens_at=opens_at, due_at=due_at, created_by=user.id)
    db.session.add(exercise)
    db.session.flush()
    audit_service.log("exercise_create", exercise, {"class": class_name, "title": title})
    db.session.commit()
    return exercise


def exercises_for(candidate: Candidate) -> list[Exercise]:
    """Published exercises only: a scheduled one stays hidden until its opening time."""
    if not candidate.sas_id:
        return []
    return db.session.scalars(_for_candidate(Exercise, candidate).where(Exercise.opens_at <= utcnow())
                              .order_by(Exercise.opens_at.desc())).all()


def exercise_roster(exercise: Exercise) -> list[Candidate]:
    stmt = select(Candidate).where(Candidate.sas_id == exercise.sas_id).order_by(Candidate.sas_class, Candidate.full_name)
    if exercise.class_name:
        stmt = stmt.where(Candidate.sas_class == exercise.class_name)
    return db.session.scalars(stmt).all()


def _submission(exercise: Exercise, candidate: Candidate) -> ExerciseSubmission:
    found = next((s for s in exercise.submissions if s.candidate_id == candidate.id), None)
    if found is None:
        found = ExerciseSubmission(candidate=candidate, answer="")
        exercise.submissions.append(found)
    return found


def submit_answer(exercise: Exercise, candidate: Candidate, answer: str) -> None:
    if exercise.sas_id != candidate.sas_id or exercise.class_name not in ("", candidate.sas_class):
        raise BusinessError("Cet exercice ne concerne pas votre classe.")
    if not exercise.accepts_answers:
        raise BusinessError("Cet exercice n'accepte plus de réponse (date limite passée).")
    answer = answer.strip()[:20000]
    if not answer:
        raise BusinessError("Votre réponse est vide.")
    submission = _submission(exercise, candidate)
    if submission.status == VALIDATED:
        raise BusinessError("Votre exercice est déjà validé.")
    submission.answer, submission.submitted_at, submission.status = answer, utcnow(), SUBMITTED
    audit_service.log("exercise_submit", exercise, {"matricule": candidate.matricule})
    db.session.commit()


def review(exercise: Exercise, candidate: Candidate, user, status: str, score: str, feedback: str) -> None:
    """The coach validates (or sends back) a student's result, with or without an answer submitted in the app."""
    if status not in (VALIDATED, REJECTED):
        raise BusinessError("Décision invalide.")
    if candidate not in exercise_roster(exercise):
        raise BusinessError("Cet étudiant ne fait pas partie de cet exercice.")
    submission = _submission(exercise, candidate)
    submission.status, submission.score, submission.feedback = status, score.strip()[:20], feedback.strip()[:5000]
    submission.reviewed_by, submission.reviewed_at = user.id, utcnow()
    audit_service.log("exercise_review", exercise, {"matricule": candidate.matricule, "status": status})
    db.session.commit()


# ---- reception

def find_student(text: str) -> Candidate | None:
    """'26CAND000012 — NOM' (datalist), a matricule or an email; only SAS students."""
    key = (text or "").strip().split(" ")[0]
    if not key:
        return None
    candidate = db.session.scalar(select(Candidate).where(
        Candidate.sas_id.is_not(None), or_(Candidate.matricule == key.upper(), func.lower(Candidate.email) == key.lower())))
    if candidate is None:
        raise BusinessError(f"Aucun étudiant du SAS ne correspond à « {key} ».")
    return candidate


def sas_students() -> list[Candidate]:
    return db.session.scalars(select(Candidate).where(Candidate.sas_id.is_not(None)).order_by(Candidate.full_name)).all()


def add_visit(user, form) -> Visit:
    name = form.get("visitor_name", "").strip()[:150]
    if not name:
        raise BusinessError("Le nom du visiteur est obligatoire.")
    visit = Visit(visitor_name=name, visitor_phone=form.get("visitor_phone", "").strip()[:30],
                  candidate=find_student(form.get("student", "")), reason=form.get("reason", "").strip()[:255],
                  created_by=user.id)
    db.session.add(visit)
    db.session.flush()
    audit_service.log("visit_add", visit, {"visitor": name})
    db.session.commit()
    return visit


def add_incident(user, form) -> Incident:
    candidate = find_student(form.get("student", ""))
    if candidate is None:
        raise BusinessError("Indiquez l'étudiant concerné.")
    description = form.get("description", "").strip()[:5000]
    if not description:
        raise BusinessError("Décrivez le problème.")
    category = form.get("category") if form.get("category") in INCIDENT_CATEGORIES else "other"
    incident = Incident(candidate=candidate, category=category, description=description, created_by=user.id)
    db.session.add(incident)
    db.session.flush()
    audit_service.log("incident_add", incident, {"matricule": candidate.matricule, "category": category})
    db.session.commit()
    return incident
