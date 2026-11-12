"""Training cohorts: eligible students (admitted at a SAS defense), desk registration, DFS matricule."""
import base64
import binascii
from datetime import date

from sqlalchemy import func, select

from app.extensions import db
from app.models import Candidate, Cohort, CohortCoach, Defense, Enrollment, Jury, JuryCandidate, User
from app.models.user import COACH_ROLES
from app.services import audit_service
from app.services.account_service import PHOTO_MAX_BYTES, image_type
from app.services.candidate_service import BusinessError, today
from app.utils import utcnow

ADMITTED = ("admitted", "reserved")  # defense decisions giving access to a training cohort


def create(form) -> Cohort:
    name = form.get("name", "").strip()[:100]
    if not name:
        raise BusinessError("Indiquez l'année académique (ex. 2026-2027).")
    if db.session.scalar(select(Cohort.id).where(Cohort.name == name)):
        raise BusinessError(f"La cohorte « {name} » existe déjà.")
    cohort = Cohort(name=name, description=form.get("description", "").strip()[:255])
    db.session.add(cohort)
    db.session.flush()
    audit_service.log("cohort_update", cohort, {"created": name})
    db.session.commit()
    return cohort


def admitted(sas_id: int) -> list[Candidate]:
    """Students declared admitted (or admitted with reservation) at a defense of this SAS, not in any cohort yet.
    ponytail: decision read in Python (JSON column), a SAS holds a few hundred students at most."""
    rows = db.session.scalars(select(JuryCandidate).join(Jury).join(Defense, Defense.id == Jury.defense_id)
                              .where(Defense.sas_id == sas_id)
                              .where(~JuryCandidate.candidate.has(Candidate.enrollment.has()))).all()
    found = {r.candidate_id: r.candidate for r in rows if (r.sheet or {}).get("decision") in ADMITTED}
    return sorted(found.values(), key=lambda c: c.full_name)


def add_eligible(cohort: Cohort, sas_id: int, candidate_ids: list[int]) -> int:
    """Only ids from admitted(): a forged form cannot bring in a non-admitted student."""
    chosen = [c for c in admitted(sas_id) if c.id in set(candidate_ids)]
    for c in chosen:
        db.session.add(Enrollment(cohort=cohort, candidate=c))
    audit_service.log("cohort_update", cohort, {"eligible": [c.matricule for c in chosen]})
    db.session.commit()
    return len(chosen)


def coach_choices(cohort: Cohort) -> list[User]:
    """Active coaches (technique or métier) not in the cohort yet."""
    taken = {cc.user_id for cc in cohort.coaches}
    users = db.session.scalars(select(User).where(User.role.in_(COACH_ROLES), User.active)
                               .order_by(User.full_name)).all()
    return [u for u in users if u.id not in taken]


def set_coach(cohort: Cohort, user_id: int, role: str) -> CohortCoach:
    """Admin: give a coach access to the cohort with his role, or change his role."""
    role = role.strip()[:100]
    if not role:
        raise BusinessError("Indiquez le rôle du coach dans la cohorte.")
    link = next((cc for cc in cohort.coaches if cc.user_id == user_id), None)
    if link is None:
        user = db.session.get(User, user_id)
        if user is None or user.role not in COACH_ROLES or not user.active:
            raise BusinessError("Choisissez un coach actif.")
        link = CohortCoach(cohort=cohort, user=user)
        db.session.add(link)
    link.role = role
    audit_service.log("cohort_update", cohort, {"coach": link.user.email, "role": role})
    db.session.commit()
    return link


def remove_coach(link: CohortCoach) -> None:
    audit_service.log("cohort_update", link.cohort, {"coach_removed": link.user.email})
    db.session.delete(link)
    db.session.commit()


def remove(enrollment: Enrollment) -> None:
    if enrollment.validated_at:
        raise BusinessError("Inscription déjà validée : l'étudiant garde son matricule DFS.")
    audit_service.log("cohort_update", enrollment.cohort, {"removed": enrollment.candidate.matricule})
    db.session.delete(enrollment)
    db.session.commit()


def _signature(data_url: str) -> bytes:
    """The PNG drawn on the signature pad (data:image/png;base64,...); empty if nothing was drawn."""
    if not data_url:
        return b""
    head, _, payload = data_url.partition(",")
    try:
        data = base64.b64decode(payload, validate=True) if head == "data:image/png;base64" else b""
    except (binascii.Error, ValueError):
        data = b""
    if not data or len(data) > PHOTO_MAX_BYTES or image_type(data) != "image/png":
        raise BusinessError("Signature illisible : signez à nouveau dans le cadre.")
    return data


def save(enrollment: Enrollment, form, validate: bool, user) -> None:
    """Desk form (reception / residence / admin): arrival, bedroom, equipment, signature; validating gives the
    DFS matricule. A validated registration stays editable (bedroom change...), its matricule never changes."""
    try:
        enrollment.arrival_date = date.fromisoformat(form["arrival_date"]) if form.get("arrival_date") else None
    except ValueError:
        raise BusinessError("Date d'arrivée invalide.") from None
    enrollment.bedroom = form.get("bedroom", "").strip()[:50]
    enrollment.equipment = form.get("equipment", "").strip()[:255]
    if signature := _signature(form.get("signature", "")):
        enrollment.signature, enrollment.signature_type = signature, "image/png"
    details = {"arrival": form.get("arrival_date"), "bedroom": enrollment.bedroom, "equipment": enrollment.equipment,
               "signed": bool(signature)}
    if validate and not enrollment.validated_at:
        if not enrollment.arrival_date or not enrollment.signature_type:
            raise BusinessError("Pour valider l'inscription : date d'arrivée et signature de l'étudiant requises.")
        enrollment.matricule = next_matricule()
        enrollment.validated_at, enrollment.validated_by = utcnow(), user.id
        details["matricule"] = enrollment.matricule
    audit_service.log("cohort_enroll", enrollment.candidate, details)
    db.session.commit()


def matricule_prefix() -> str:
    return f"{today():%y}DFS"  # 2026 -> "26DFS", like 26CAND for the candidates


def next_matricule() -> str:
    """26DFS000001, 26DFS000002... in validation order.
    ponytail: max+1, two simultaneous validations can collide -> the UNIQUE constraint rejects the second one."""
    prefix = matricule_prefix()
    last = db.session.scalar(select(func.max(Enrollment.matricule)).where(Enrollment.matricule.like(f"{prefix}%")))
    return f"{prefix}{(int(last[len(prefix):]) if last else 0) + 1:06d}"
