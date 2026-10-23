"""SAS campus: who manages which class, notifications, exercises, visits, incidents."""
import re
import secrets
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo

from sqlalchemy import func, or_, select

from app.extensions import db
from app.models import (AttendanceSession, Candidate, ClassInvitation, Exercise, ExerciseAnswer, ExerciseSubmission,
                        Incident, Media, Notification, Sas, SasClass, User, Visit)
from app.models.campus import (INCIDENT_CATEGORIES, INVITE_ACCEPTED, INVITE_DECLINED, INVITE_PENDING, KIND_VISIT,
                               REJECTED, SUBMITTED, VALIDATED, VISIT_KINDS)
from app.models.user import ROLE_ADMIN, ROLE_COACH, ROLE_RECEPTION
from app.services import account_service, audit_service, settings_service
from app.services.candidate_service import BusinessError
from app.utils import as_utc, utcnow


# ---- classes and rights

def class_choices() -> list[dict]:
    """Every (SAS, class): created by a coach (maybe still empty) or holding students.
    [{key, sas, class_name, count}]"""
    counts = {(sas_id, name): n for sas_id, name, n in db.session.execute(
        select(Candidate.sas_id, Candidate.sas_class, func.count(Candidate.id))
        .where(Candidate.sas_id.is_not(None), Candidate.sas_class.is_not(None), Candidate.sas_class != "")
        .group_by(Candidate.sas_id, Candidate.sas_class))}
    keys = set(counts) | set(db.session.execute(select(SasClass.sas_id, SasClass.name)).tuples())
    sas_by_id = {s.id: s for s in db.session.scalars(select(Sas).where(Sas.id.in_({k[0] for k in keys})))}
    choices = [{"key": f"{sas_id}:{name}", "sas": sas_by_id[sas_id], "class_name": name,
                "count": counts.get((sas_id, name), 0)} for sas_id, name in keys]
    return sorted(choices, key=lambda c: (c["sas"].name, c["class_name"]))


def can_manage(user, sas_id: int, class_name: str) -> bool:
    """Admin and reception: everything. Coach: his own classes (not a whole SAS)."""
    if user.role in (ROLE_ADMIN, ROLE_RECEPTION):
        return True
    return user.role == ROLE_COACH and bool(class_name) and f"{sas_id}:{class_name}" in (user.coach_classes or [])


def targets(user) -> list[dict]:
    """Classes (and whole SAS for admin / reception) the user may address: [{key, label}]."""
    out = []
    if user.role in (ROLE_ADMIN, ROLE_RECEPTION):
        out = [{"key": f"{s.id}:", "label": f"{s.name} — tout le SAS"}
               for s in db.session.scalars(select(Sas).order_by(Sas.name))]
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


def local_now_input() -> str:
    """Now, in the school's time zone, formatted for <input type="datetime-local">."""
    return utcnow().astimezone(ZoneInfo(settings_service.get("timezone") or "UTC")).strftime("%Y-%m-%dT%H:%M")


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
    notification = Notification(sas=sas, class_name=class_name, title=title, body=body.strip()[:5000],
                                created_by=user.id)
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
    exercise = Exercise(sas=sas, class_name=class_name, title=title,
                        instructions=form.get("instructions", "").strip()[:20000],
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
    stmt = (select(Candidate).where(Candidate.sas_id == exercise.sas_id)
            .order_by(Candidate.sas_class, Candidate.full_name))
    if exercise.class_name:
        stmt = stmt.where(Candidate.sas_class == exercise.class_name)
    return db.session.scalars(stmt).all()


def _submission(exercise: Exercise, candidate: Candidate) -> ExerciseSubmission:
    found = next((s for s in exercise.submissions if s.candidate_id == candidate.id), None)
    if found is None:
        found = ExerciseSubmission(candidate=candidate, answer="")
        exercise.submissions.append(found)
    return found


def update_exercise(exercise: Exercise, user, form) -> None:
    """The coach corrects the title, the statement and the deadline (answers already handed in are kept)."""
    title = form.get("title", "").strip()[:150]
    if not title:
        raise BusinessError("Le titre est obligatoire.")
    due_at = local_datetime(form.get("due_at", ""))
    if due_at and due_at <= as_utc(exercise.opens_at):
        raise BusinessError("La date limite doit être après la publication.")
    changed = [label for label, old, new in (("titre", exercise.title, title),
                                             ("énoncé", exercise.instructions, form.get("instructions", "").strip()),
                                             ("date limite", exercise.due_at and as_utc(exercise.due_at), due_at))
               if old != new]
    exercise.title, exercise.due_at = title, due_at
    exercise.instructions = form.get("instructions", "").strip()[:20000]
    audit_service.log("exercise_update", exercise, {"changed": changed})
    db.session.commit()


def can_submit(exercise: Exercise, submission: ExerciseSubmission | None) -> bool:
    """First answer: published and before the deadline. Afterwards only with the coach's permission
    (which also works after the deadline)."""
    if not exercise.is_published:
        return False
    if submission is not None and submission.resubmit_allowed:
        return True
    if submission is not None and (submission.attempts or submission.status == VALIDATED):
        return False
    return exercise.accepts_answers


def submit_answer(exercise: Exercise, candidate: Candidate, answer: str) -> None:
    if exercise.sas_id != candidate.sas_id or exercise.class_name not in ("", candidate.sas_class):
        raise BusinessError("Cet exercice ne concerne pas votre classe.")
    submission = next((s for s in exercise.submissions if s.candidate_id == candidate.id), None)
    if not can_submit(exercise, submission):
        if submission is not None and (submission.attempts or submission.status == VALIDATED):
            raise BusinessError("Vous avez déjà rendu cet exercice : une nouvelle soumission doit être autorisée "
                                "par votre coach.")
        raise BusinessError("Cet exercice n'accepte plus de réponse (date limite passée).")
    answer = answer.strip()[:20000]
    if not answer:
        raise BusinessError("Votre réponse est vide.")
    submission = _submission(exercise, candidate)
    now = utcnow()
    submission.attempts.append(ExerciseAnswer(number=len(submission.attempts) + 1, answer=answer, submitted_at=now))
    submission.answer, submission.submitted_at, submission.status = answer, now, SUBMITTED
    submission.score, submission.feedback, submission.resubmit_allowed = "", "", False  # previous ones: in history
    audit_service.log("exercise_submit", exercise, {"matricule": candidate.matricule,
                                                    "attempt": len(submission.attempts)})
    db.session.commit()


def review(exercise: Exercise, candidate: Candidate, user, status: str, score: str, feedback: str) -> None:
    """The coach validates (or sends back) a student's result, with or without an answer submitted in the app.
    The review is also stored on the latest answer (history); « À refaire » allows a new submission."""
    if status not in (VALIDATED, REJECTED):
        raise BusinessError("Décision invalide.")
    if candidate not in exercise_roster(exercise):
        raise BusinessError("Cet étudiant ne fait pas partie de cet exercice.")
    submission = _submission(exercise, candidate)
    now, score, feedback = utcnow(), score.strip()[:20], feedback.strip()[:5000]
    for target in (submission, *submission.attempts[-1:]):
        target.status, target.score, target.feedback, target.reviewed_by, target.reviewed_at = \
            status, score, feedback, user.id, now
    submission.resubmit_allowed = status == REJECTED
    audit_service.log("exercise_review", exercise, {"matricule": candidate.matricule, "status": status})
    db.session.commit()


def set_resubmission(exercise: Exercise, candidate: Candidate, user, allowed: bool) -> None:
    """Coach's permission for one more submission (the history of the previous ones is kept)."""
    if candidate not in exercise_roster(exercise):
        raise BusinessError("Cet étudiant ne fait pas partie de cet exercice.")
    _submission(exercise, candidate).resubmit_allowed = allowed
    audit_service.log("exercise_resubmit", exercise, {"matricule": candidate.matricule, "allowed": allowed,
                                                      "by": user.id})
    db.session.commit()


# ---- images of the rich editor (exercise statements)

MEDIA_MAX_BYTES = 5 * 1024 * 1024


def save_media(user, upload) -> Media:
    data = upload.read(MEDIA_MAX_BYTES + 1) if upload else b""
    if not data:
        raise BusinessError("Aucune image reçue.")
    if len(data) > MEDIA_MAX_BYTES:
        raise BusinessError("Image trop lourde : 5 Mo maximum.")
    mimetype = account_service.image_type(data)  # real format from the bytes; SVG (scripts) refused
    if mimetype is None:
        raise BusinessError("Image invalide : formats JPEG, PNG ou WebP uniquement.")
    media = Media(key=secrets.token_hex(16), data=data, mimetype=mimetype, size=len(data), created_by=user.id)
    db.session.add(media)
    db.session.flush()
    audit_service.log("media_upload", media, {"size": len(data), "type": mimetype})
    db.session.commit()
    return media


# ---- reception

def find_student(text: str, sas_only: bool = True) -> Candidate | None:
    """'26CAND000012 — NOM' (datalist), a matricule or an email; SAS students only unless sas_only=False."""
    key = (text or "").strip().split(" ")[0]
    if not key:
        return None
    stmt = select(Candidate).where(or_(Candidate.matricule == key.upper(), func.lower(Candidate.email) == key.lower()))
    candidate = db.session.scalar(stmt.where(Candidate.sas_id.is_not(None)) if sas_only else stmt)
    if candidate is None:
        who = "étudiant du SAS" if sas_only else "candidat ou étudiant"
        raise BusinessError(f"Aucun {who} ne correspond à « {key} ».")
    return candidate


def sas_students(sas_only: bool = True) -> list[Candidate]:
    stmt = select(Candidate).order_by(Candidate.full_name)
    return db.session.scalars(stmt.where(Candidate.sas_id.is_not(None)) if sas_only else stmt).all()


def add_visit(user, form) -> Visit:
    """Site register entry. Everyone: name, origin, ID type + number. Visit: the student visited.
    Other activity: its category and the activity."""
    value = lambda key, size: " ".join(form.get(key, "").split())[:size]  # noqa: E731
    kind = form.get("kind") if form.get("kind") in VISIT_KINDS else KIND_VISIT
    visit = Visit(kind=kind, visitor_name=value("visitor_name", 150), visitor_phone=value("visitor_phone", 30),
                  origin=value("origin", 120), id_type=value("id_type", 40), id_number=value("id_number", 50).upper(),
                  created_by=user.id)
    missing = [label for label, v in (("nom", visit.visitor_name), ("provenance", visit.origin),
                                      ("type de pièce", visit.id_type), ("numéro de pièce", visit.id_number)) if not v]
    if missing:
        raise BusinessError("Champs obligatoires : " + ", ".join(missing) + ".")
    if kind == KIND_VISIT:
        visit.candidate = find_student(form.get("student", ""), sas_only=False)
        if visit.candidate is None:
            raise BusinessError("Visite : précisez le candidat ou l'étudiant visité.")
        visit.reason = value("reason", 255)
    else:
        visit.activity_category, visit.activity = value("activity_category", 40), value("activity", 255)
        if not (visit.activity_category and visit.activity):
            raise BusinessError("Autre activité : précisez la catégorie et l'activité.")
    db.session.add(visit)
    db.session.flush()
    audit_service.log("visit_add", visit, {"kind": kind, "visitor": visit.visitor_name})
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


# ---- classes: creation, join code (QR / address), invitations

JOIN_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"  # no 0/O, 1/I/L: typed by hand from a projected screen


def _new_join_code() -> str:
    while True:
        code = "".join(secrets.choice(JOIN_ALPHABET) for _ in range(8))
        if not db.session.scalar(select(SasClass.id).where(SasClass.join_code == code)):
            return code


def get_class(sas_id: int, name: str, user=None) -> SasClass:
    """The class row, created on first use (classes assigned by import have none yet)."""
    klass = db.session.scalar(select(SasClass).where(SasClass.sas_id == sas_id, SasClass.name == name))
    if klass is None:
        klass = SasClass(sas_id=sas_id, name=name, join_code=_new_join_code(), created_by=getattr(user, "id", None))
        db.session.add(klass)
        db.session.commit()
    return klass


def create_class(user, sas_id: int, name: str) -> SasClass:
    name = " ".join(name.split())[:50]
    sas = db.session.get(Sas, sas_id or 0)
    if sas is None or not name:
        raise BusinessError("Choisissez un SAS et un nom de classe.")
    if user.role not in (ROLE_ADMIN, ROLE_COACH):  # a coach's new class becomes one of his classes
        raise BusinessError("Création de classe non autorisée.")
    taken = db.session.scalar(select(SasClass.id).where(SasClass.sas_id == sas.id, SasClass.name == name))
    used = db.session.scalar(select(Candidate.id).limit(1)
                             .where(Candidate.sas_id == sas.id, Candidate.sas_class == name))
    if taken or used:
        raise BusinessError(f"La classe « {name} » existe déjà dans {sas.name}.")
    klass = SasClass(sas=sas, name=name, join_code=_new_join_code(), created_by=user.id)
    db.session.add(klass)
    if user.role == ROLE_COACH:
        user.coach_classes = sorted({*(user.coach_classes or []), f"{sas.id}:{name}"})  # new list: JSON change tracked
    db.session.flush()
    audit_service.log("class_create", klass, {"sas": sas.name, "name": name})
    db.session.commit()
    return klass


def renew_join_code(klass: SasClass) -> None:
    """The old QR code / address stops working (shared too widely)."""
    klass.join_code = _new_join_code()
    audit_service.log("class_code_renew", klass)
    db.session.commit()


def class_members(klass: SasClass) -> list[Candidate]:
    return db.session.scalars(select(Candidate).where(Candidate.sas_id == klass.sas_id,
                                                      Candidate.sas_class == klass.name)
                              .order_by(Candidate.full_name)).all()


def class_from_code(text: str) -> SasClass:
    """The bare code or the scanned / typed address (…?code=XXXX)."""
    text = (text or "").strip()[:500]
    if "code=" in text:
        text = (parse_qs(urlsplit(text).query).get("code") or [""])[0]
    klass = db.session.scalar(select(SasClass).where(SasClass.join_code == text.strip().upper())) if text else None
    if klass is None:
        raise BusinessError("Code de classe inconnu : vérifiez l'adresse ou demandez le nouveau code à votre coach.")
    return klass


def _enter(klass: SasClass, candidate: Candidate, how: str) -> None:
    if candidate.sas_id != klass.sas_id:
        raise BusinessError(f"Cette classe appartient au SAS « {klass.sas.name} » : vous n'y êtes pas admis.")
    candidate.sas_class = klass.name
    for invitation in klass.invitations:
        if invitation.candidate_id == candidate.id and invitation.status == INVITE_PENDING:
            invitation.status, invitation.responded_at = INVITE_ACCEPTED, utcnow()
    audit_service.log("class_join", klass, {"matricule": candidate.matricule, "how": how})
    db.session.commit()


def join_with_code(candidate: Candidate, code: str) -> SasClass:
    klass = class_from_code(code)
    _enter(klass, candidate, "code")
    return klass


def invite(klass: SasClass, user, text: str) -> tuple[int, list[str]]:
    """Invite by matricules / emails. Returns (invited, problems)."""
    keys = {k for k in re.split(r"[\s,;]+", text or "") if k}
    found = db.session.scalars(select(Candidate).where(or_(
        Candidate.matricule.in_({k.upper() for k in keys}),
        func.lower(Candidate.email).in_({k.lower() for k in keys})))).all()
    known = {c.matricule for c in found} | {(c.email or "").lower() for c in found}
    problems = [f"{k} : introuvable" for k in sorted(keys) if k.upper() not in known and k.lower() not in known]
    existing = {i.candidate_id: i for i in klass.invitations}
    invited = 0
    for c in found:
        if c.sas_id != klass.sas_id:
            problems.append(f"{c.matricule} : pas admis dans {klass.sas.name}")
        elif c.sas_class == klass.name:
            problems.append(f"{c.matricule} : déjà dans la classe")
        elif existing.get(c.id) and existing[c.id].status == INVITE_PENDING:
            problems.append(f"{c.matricule} : déjà invité(e)")
        else:
            invitation = existing.get(c.id) or ClassInvitation(candidate=c)
            invitation.status, invitation.invited_by, invitation.created_at, invitation.responded_at = \
                INVITE_PENDING, user.id, utcnow(), None
            if invitation not in klass.invitations:
                klass.invitations.append(invitation)
            invited += 1
    if invited:
        audit_service.log("class_invite", klass, {"invited": invited})
    db.session.commit()
    return invited, problems


def pending_invitations(candidate: Candidate) -> list[ClassInvitation]:
    return db.session.scalars(select(ClassInvitation).where(
        ClassInvitation.candidate_id == candidate.id, ClassInvitation.status == INVITE_PENDING)
        .order_by(ClassInvitation.created_at.desc())).all()


def answer_invitation(invitation: ClassInvitation, candidate: Candidate, accept: bool) -> None:
    if invitation.candidate_id != candidate.id or invitation.status != INVITE_PENDING:
        raise BusinessError("Cette invitation n'est plus valable.")
    if accept:
        _enter(invitation.klass, candidate, "invitation")
        return
    invitation.status, invitation.responded_at = INVITE_DECLINED, utcnow()
    audit_service.log("class_decline", invitation.klass, {"matricule": candidate.matricule})
    db.session.commit()


# ---- class reports (admin overview, class page)

def coaches_by_class() -> dict[str, list[User]]:
    """'sas_id:class' -> coaches teaching it (from each coach's classes)."""
    out: dict[str, list[User]] = {}
    for coach in db.session.scalars(select(User).where(User.role == ROLE_COACH).order_by(User.full_name, User.email)):
        for key in coach.coach_classes or []:
            out.setdefault(key, []).append(coach)
    return out


def class_counts() -> tuple[dict, dict]:
    """(exercises, roll calls) per (sas_id, class name), two grouped queries for the whole list."""
    exercises = dict(((s, n), c) for s, n, c in db.session.execute(
        select(Exercise.sas_id, Exercise.class_name, func.count(Exercise.id))
        .group_by(Exercise.sas_id, Exercise.class_name)))
    calls = dict(((s, n), c) for s, n, c in db.session.execute(
        select(AttendanceSession.sas_id, AttendanceSession.class_name, func.count(AttendanceSession.id))
        .group_by(AttendanceSession.sas_id, AttendanceSession.class_name)))
    return exercises, calls


def class_report(klass: SasClass) -> dict:
    """Everything about one class: coaches, exercises with their results, roll calls, notifications."""
    members = class_members(klass)
    ids = {c.id for c in members}
    exercises = []
    stmt = select(Exercise).where(Exercise.sas_id == klass.sas_id, Exercise.class_name == klass.name)
    for e in db.session.scalars(stmt.order_by(Exercise.opens_at.desc())):
        subs = [s for s in e.submissions if s.candidate_id in ids]
        exercises.append({
            "exercise": e,
            "handed_in": sum(1 for s in subs if s.attempts),
            "attempts": sum(len(s.attempts) for s in subs),
            "to_review": sum(1 for s in subs if s.attempts and not s.attempts[-1].reviewed_at),
            "validated": sum(1 for s in subs if s.status == VALIDATED),
            "rejected": sum(1 for s in subs if s.status == REJECTED),
        })
    calls = [{"session": s, "present": sum(1 for m in s.marks if m.candidate_id in ids)}
             for s in db.session.scalars(select(AttendanceSession).where(
                 AttendanceSession.sas_id == klass.sas_id, AttendanceSession.class_name == klass.name)
                 .order_by(AttendanceSession.opened_at.desc()).limit(30))]
    notifications = db.session.scalars(select(Notification).where(
        Notification.sas_id == klass.sas_id, Notification.class_name == klass.name)
        .order_by(Notification.created_at.desc()).limit(10)).all()
    creator = db.session.get(User, klass.created_by) if klass.created_by else None
    return {"members": members, "exercises": exercises, "calls": calls, "notifications": notifications,
            "coaches": coaches_by_class().get(klass.key, []), "creator": creator,
            "absent_hours": sum(c["session"].hours * (len(members) - c["present"]) for c in calls
                                if c["session"].is_closed)}
