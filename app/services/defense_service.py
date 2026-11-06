"""SAS defenses: juries, their members (staff, one jury per person and defense) and the students they hear."""
import re
from datetime import timedelta

from sqlalchemy import func, or_, select

from app.extensions import db
from app.models import Candidate, Defense, DefenseScore, Jury, JuryCandidate, JuryMember, Sas, User
from app.models.defense import (DECISIONS, DEFENSE_CRITERIA, DEFENSE_SCALE, DEFENSE_SECTIONS, JURY_ROLES, NEGATIVES,
                                POSITIVES, SUPPORTS, grid_points)
from app.models.user import ROLE_ADMIN, ROLE_CANDIDATE
from app.services import attendance_service, audit_service
from app.services.account_service import PHOTO_MAX_BYTES, image_type
from app.services.campus_service import evaluation_of, local_datetime
from app.services.candidate_service import BusinessError
from app.utils import utcnow

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


# ---- defense sheet (fiche globale d'évaluation): ratings by role, shared part, results

def can_view(row: JuryCandidate, user) -> bool:
    """The jury's members and the admin."""
    return user.role == ROLE_ADMIN or row.jury.member_of(user) is not None


def _unlocked(jury: Jury) -> None:
    if jury.validated_at:
        raise BusinessError("Délibération validée : la fiche n'est plus modifiable.")


def save_score(row: JuryCandidate, user, form) -> DefenseScore:
    """A member rates the whole grid, whatever his role in the jury.
    A 1 or 4 rating needs a written fact, as on the paper grid."""
    _unlocked(row.jury)
    member = row.jury.member_of(user)
    if member is None:
        raise BusinessError("Seuls les membres du jury notent cette soutenance.")
    scale = {value for value, _l, _h in DEFENSE_SCALE}
    ratings, evidence = {}, {}
    for _section, key, _title, _ind, _weight in DEFENSE_CRITERIA:
        if (rating := form.get(f"r_{key}", "")) in scale:
            ratings[key] = rating
        if text := form.get(f"e_{key}", "").strip()[:500]:
            evidence[key] = text
    missing =[title for _s, key, title, _i, _w in DEFENSE_CRITERIA if ratings.get(key) in ("1", "4")
               and not evidence.get(key)]
    if missing:
        raise BusinessError("Une cote 1 ou 4 doit s'appuyer sur un fait cité en commentaire : "
                            + ", ".join(missing) + ".")
    score = next((s for s in row.scores if s.user_id == user.id), None)
    if score is None:
        score = DefenseScore(row=row, user_id=user.id)
        db.session.add(score)
    score.ratings, score.evidence, score.updated_at = ratings, evidence, utcnow()
    audit_service.log("defense_score", row.candidate, {"jury": row.jury.name, "role": member.role, "ratings": ratings})
    db.session.commit()
    return score


def _minutes(form, key: str) -> int | None:
    value = form.get(key, type=int)
    return value if value is not None and 0 <= value <= 600 else None


def save_sheet(row: JuryCandidate, user, form) -> None:
    """Shared part, filled by any jury member or the admin: "header" (subject, times, format, supports)
    or "appraisal" (quick observations, strengths, improvements, decision)."""
    _unlocked(row.jury)
    if not can_view(row, user):
        raise BusinessError("Seuls les membres du jury remplissent cette fiche.")
    sheet = dict(row.sheet or {})  # new dict: SQLAlchemy only sees a replaced JSON value
    if form.get("part") == "header":
        sheet.update(subject=_text(form, "subject", 300), team=_text(form, "team", 150),
                     format=form.get("format") if form.get("format") in ("individual", "team") else "",
                     supports=[k for k in form.getlist("supports") if k in SUPPORTS])
        sheet.update({k: _minutes(form, k) for k in ("talk_minutes", "questions_minutes", "real_minutes",
                                                     "team_size")})
    elif form.get("part") == "appraisal":
        sheet.update(positives=[k for k in form.getlist("positives") if k in POSITIVES],
                     negatives=[k for k in form.getlist("negatives") if k in NEGATIVES],
                     other_positive=_text(form, "other_positive", 200),
                     other_negative=_text(form, "other_negative", 200),
                     strengths=form.get("strengths", "").strip()[:1500],
                     improvements=form.get("improvements", "").strip()[:1500],
                     decision=form.get("decision") if form.get("decision") in DECISIONS else "",
                     decision_note=_text(form, "decision_note", 200))
    row.sheet = sheet
    audit_service.log("defense_score", row.candidate, {"jury": row.jury.name, "sheet": form.get("part")})
    db.session.commit()


def results(row: JuryCandidate) -> dict:
    """Section 3 (each member's sections), the jury average per section, the defense score /100, the
    follow-up score (individual sheet) and the final grade = average of both."""
    by_user = {s.user_id: s for s in row.scores}
    lines, per_section, complete = [], {s: [] for s in DEFENSE_SECTIONS}, bool(row.jury.members)
    keys = {c[1] for c in DEFENSE_CRITERIA}
    for member in row.jury.members:
        score, parts = by_user.get(member.user_id), {}
        for section in DEFENSE_SECTIONS:
            points, weight = score.section(section) if score else (0, 0)
            if weight:
                parts[section] = (points, weight)
                per_section[section].append((points, weight))
        complete = complete and score is not None and keys <= set(score.ratings)
        lines.append((member, parts, sum(p for p, _ in parts.values())))
    sections = {s: (sum(p for p, _ in v) / len(v), sum(w for _, w in v) / len(v))
                for s, v in per_section.items() if v}
    points, weight = sum(p for p, _ in sections.values()), sum(w for _, w in sections.values())
    defense = round(100 * points / weight) if weight else None
    follow = evaluation_of(row.candidate).score
    final = round((defense + follow) / 2, 1) if defense is not None and follow is not None else None
    # Comparable: members who rated every section, each total out of 100 (N/A criteria left out).
    totals = [100 * total / sum(w for _p, w in parts.values()) for _m, parts, total in lines
              if len(parts) == len(DEFENSE_SECTIONS)]
    detail = {}  # per criterion: the cotes given, average points, cited facts (consolidated grid)
    for _section, key, _title, _ind, criterion_weight in DEFENSE_CRITERIA:
        raters = [(m, s) for m in row.jury.members if (s := by_user.get(m.user_id))]
        pts = [p for _m, s in raters if (p := grid_points(s.ratings, key, criterion_weight)) is not None]
        detail[key] = {"cotes": {s.ratings.get(key) for _m, s in raters if s.ratings.get(key)},
                       "points": sum(pts) / len(pts) if pts else None,
                       "facts": [(m, s.evidence[key]) for m, s in raters if s.evidence.get(key)]}
    return {"lines": lines, "sections": sections, "points": points, "weight": weight, "defense": defense,
            "follow": follow, "final": final, "complete": complete, "detail": detail,
            "spread": len(totals) > 1 and max(totals) - min(totals) > 15}


def attendance(candidate: Candidate) -> tuple[int, int]:
    """(hours present, hours of the closed roll calls) of the student."""
    sessions = attendance_service.sessions_for(candidate)
    total = sum(s.hours for s in sessions if s.is_closed)
    return total - attendance_service.absence_hours(candidate, sessions), total


def published_results(candidate: Candidate) -> list[tuple[JuryCandidate, dict]]:
    """What the student may see: only after the admin validated the jury's deliberation."""
    rows = db.session.scalars(select(JuryCandidate).join(Jury).where(
        JuryCandidate.candidate_id == candidate.id, Jury.validated_at.is_not(None))
        .order_by(Jury.validated_at.desc())).all()
    return [(row, results(row)) for row in rows]


def validate(jury: Jury, user, reopen: bool = False) -> None:
    """Admin: validate the deliberation (sheets locked, results published) or reopen it."""
    jury.validated_at, jury.validated_by = (None, None) if reopen else (utcnow(), user.id)
    jury.timer_ends_at = jury.timer_left = None
    audit_service.log("jury_validate", jury.defense, {"jury": jury.name, "reopened": reopen})
    db.session.commit()


def set_timer(jury: Jury, user, action: str, minutes: int | None = None) -> None:
    """Shared timer of a jury, set by any member (or the admin): start N minutes, pause, resume, reset."""
    if user.role != ROLE_ADMIN and jury.member_of(user) is None:
        raise BusinessError("Seuls les membres du jury pilotent le minuteur.")
    state, left = jury.timer_state()
    if action == "start":
        if minutes is None or not 1 <= minutes <= 240:
            raise BusinessError("Minuteur : entre 1 et 240 minutes.")
        jury.timer_ends_at, jury.timer_left = utcnow() + timedelta(minutes=minutes), None
    elif action == "pause" and state == "running":
        jury.timer_ends_at, jury.timer_left = None, left
    elif action == "resume" and state == "paused":
        jury.timer_ends_at, jury.timer_left = utcnow() + timedelta(seconds=left), None
    elif action == "reset":
        jury.timer_ends_at = jury.timer_left = None
    db.session.commit()


def save_signature(owner: User, file=None, remove: bool = False) -> None:
    """A jury member's signature (JPEG / PNG / WebP image, real format checked; no SVG: it can carry scripts)."""
    if remove:
        owner.signature = owner.signature_type = None
    else:
        data = file.read(PHOTO_MAX_BYTES + 1) if file is not None and file.filename else b""
        if not data:
            raise BusinessError("Choisissez l'image de la signature.")
        if len(data) > PHOTO_MAX_BYTES:
            raise BusinessError("Signature trop lourde : 2 Mo maximum.")
        if image_type(data) is None:
            raise BusinessError("Signature invalide : formats JPEG, PNG ou WebP uniquement.")
        owner.signature, owner.signature_type = data, image_type(data)
    audit_service.log("signature_update", owner, {"removed": remove})
    db.session.commit()
