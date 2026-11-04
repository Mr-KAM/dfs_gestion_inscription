"""SAS defenses: juries, their members (staff, one jury per person and defense) and the students they hear."""
import re

from sqlalchemy import func, or_, select

from app.extensions import db
from app.models import Candidate, Defense, Jury, JuryCandidate, JuryMember, Sas, User
from app.models.defense import JURY_ROLES
from app.models.user import ROLE_CANDIDATE
from app.services import audit_service
from app.services.campus_service import local_datetime
from app.services.candidate_service import BusinessError

MIN_DURATION, MAX_DURATION = 5, 480


def _text(form, key: str, size: int) -> str:
    return " ".join(form.get(key, "").split())[:size]


def save_defense(user, form, defense: Defense | None = None) -> Defense:
    if defense is None:
        sas = db.session.get(Sas, form.get("sas_id", type=int) or 0)
        if sas is None:
            raise BusinessError("Choisissez un SAS.")
        defense = Defense(sas=sas, created_by=user.id)
        db.session.add(defense)
    defense.name = _text(form, "name", 150)
    if not defense.name:
        raise BusinessError("Le nom de la soutenance est obligatoire.")
    defense.starts_at = local_datetime(form.get("starts_at", ""))
    defense.location = _text(form, "location", 150)
    db.session.flush()
    audit_service.log("defense_update", defense, {"name": defense.name})
    db.session.commit()
    return defense


def save_jury(defense: Defense, form, jury: Jury | None = None) -> Jury:
    name = _text(form, "name", 100)
    if not name:
        raise BusinessError("Le nom du jury est obligatoire.")
    duration = form.get("duration", type=int)
    if duration is None or not MIN_DURATION <= duration <= MAX_DURATION:
        raise BusinessError(f"Durée d'une soutenance : entre {MIN_DURATION} et {MAX_DURATION} minutes.")
    if jury is None:
        jury = Jury(defense=defense)
        db.session.add(jury)
    jury.name, jury.duration, jury.room = name, duration, _text(form, "room", 100)
    jury.starts_at = local_datetime(form.get("starts_at", ""))
    db.session.flush()
    audit_service.log("defense_update", defense, {"jury": name, "duration": duration})
    db.session.commit()
    return jury


def staff_choices(defense: Defense) -> list[User]:
    """Active team members not yet in a jury of this defense."""
    taken = select(JuryMember.user_id).where(JuryMember.defense_id == defense.id)
    return db.session.scalars(select(User).where(User.role != ROLE_CANDIDATE, User.active.is_(True),
                                                 User.id.not_in(taken))
                              .order_by(User.full_name, User.email)).all()


def add_member(jury: Jury, user_id: int, role: str) -> JuryMember:
    user = db.session.get(User, user_id or 0)
    if user is None or user.role == ROLE_CANDIDATE or not user.active:
        raise BusinessError("Choisissez une personne de l'équipe.")
    if role not in JURY_ROLES:
        raise BusinessError("Rôle inconnu.")
    current = db.session.scalar(select(JuryMember).where(JuryMember.defense_id == jury.defense_id,
                                                         JuryMember.user_id == user.id))
    if current:
        raise BusinessError(f"{user.display_name} est déjà dans le jury « {current.jury.name} ».")
    member = JuryMember(jury=jury, defense_id=jury.defense_id, user=user, role=role)
    db.session.add(member)
    audit_service.log("defense_update", jury.defense, {"jury": jury.name, "member": user.email, "role": role})
    db.session.commit()
    return member


def load_candidates(jury: Jury, text: str, class_name: str = "") -> tuple[int, list[str]]:
    """Add students by matricule / email (pasted list) and / or a whole class, in that order.
    (added, problems): only students of the defense's SAS, each in one jury of the defense."""
    sas_id = jury.defense.sas_id
    keys = list(dict.fromkeys(k for k in re.split(r"[\s,;]+", text or "") if k))
    found = db.session.scalars(select(Candidate).where(or_(
        Candidate.matricule.in_({k.upper() for k in keys}),
        func.lower(Candidate.email).in_({k.lower() for k in keys})))).all() if keys else []
    by_key = {c.matricule: c for c in found} | {(c.email or "").lower(): c for c in found}
    problems, wanted = [], []
    for key in keys:
        c = by_key.get(key.upper()) or by_key.get(key.lower())
        if c is None:
            problems.append(f"{key} : introuvable")
        elif c.sas_id != sas_id:
            problems.append(f"{c.full_name} : pas admis dans {jury.defense.sas.name}")
        else:
            wanted.append(c)
    if class_name:
        wanted += db.session.scalars(select(Candidate).where(Candidate.sas_id == sas_id,
                                                             Candidate.sas_class == class_name)
                                     .order_by(Candidate.full_name)).all()
    placed = {row.candidate_id: row.jury for row in db.session.scalars(
        select(JuryCandidate).where(JuryCandidate.defense_id == jury.defense_id))}
    position = max((row.position for row in jury.candidates), default=0)
    added = 0
    for c in dict.fromkeys(wanted):  # same student twice in the list: once
        if c.id in placed:
            if placed[c.id].id != jury.id:
                problems.append(f"{c.full_name} : déjà dans le jury « {placed[c.id].name} »")
            continue
        position += 1
        db.session.add(JuryCandidate(jury=jury, defense_id=jury.defense_id, candidate=c, position=position))
        placed[c.id] = jury
        added += 1
    if added:
        audit_service.log("defense_update", jury.defense, {"jury": jury.name, "candidates_added": added})
    db.session.commit()
    return added, problems


def move_candidate(row: JuryCandidate, step: int) -> None:
    """Passage order: swap with the previous (-1) or next (+1) student."""
    rows = list(row.jury.candidates)
    i = rows.index(row)
    j = i + step
    if 0 <= j < len(rows):
        rows[i].position, rows[j].position = rows[j].position, rows[i].position
        db.session.commit()


def unassigned(defense: Defense) -> list[Candidate]:
    """Students of the SAS who are in no jury of this defense yet."""
    placed = select(JuryCandidate.candidate_id).where(JuryCandidate.defense_id == defense.id)
    return db.session.scalars(select(Candidate).where(Candidate.sas_id == defense.sas_id,
                                                      Candidate.id.not_in(placed))
                              .order_by(Candidate.sas_class, Candidate.full_name)).all()
