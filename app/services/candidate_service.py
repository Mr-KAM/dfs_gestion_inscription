"""Candidate queries, filters, motivation validation and exam codes."""
import re
from datetime import date, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import case, exists, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from app.extensions import db
from app.models import Candidate, KoboSubmission, TechnicalTestSession, User
from app.models.candidate import FILE_MISSING, FILE_NOT_FOUND, FILE_PENDING, FILE_VALIDATED
from app.models.kobo import MATCH_AMBIGUOUS
from app.models.technical_test import ACTIVE_STATUSES, STATUS_PAUSED, STATUS_RUNNING
from app.models.user import ROLE_ADMIN
from app.services import audit_service, settings_service
from app.utils import utcnow

EXAM_CODE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_\-/.]{0,49}$")


class BusinessError(Exception):
    """A rule violation with a message safe to show to the user."""


def today() -> date:
    return datetime.now(ZoneInfo(settings_service.get("timezone") or "UTC")).date()


def _active_session_exists():
    return exists().where(
        TechnicalTestSession.candidate_id == Candidate.id, TechnicalTestSession.status.in_(ACTIVE_STATUSES)
    )


SORTS = {
    "number": Candidate.candidate_number,
    "group": Candidate.group_name,
    "date": Candidate.scheduled_date,
    "order": Candidate.passage_order,
    "name": Candidate.full_name,
    "exam_code": Candidate.exam_code,
    "city": Candidate.city,
}


def filtered_query(args, eligible_only: bool = False):
    """Build the candidate list query from request args (search, filters, sort)."""
    stmt = select(Candidate).options(
        selectinload(Candidate.sessions).selectinload(TechnicalTestSession.workstation),
        selectinload(Candidate.sessions).selectinload(TechnicalTestSession.room),
    )
    if eligible_only:
        stmt = stmt.where(Candidate.motivation_completed.is_(True))

    q = (args.get("q") or "").strip()
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(
            Candidate.full_name.ilike(like), Candidate.email.ilike(like), Candidate.phone.ilike(like),
            Candidate.cni.ilike(like), Candidate.exam_code.ilike(like), Candidate.piece_number.ilike(like),
            Candidate.candidate_number.ilike(like),
        ))
    if args.get("group"):
        stmt = stmt.where(Candidate.group_name == args["group"])
    if args.get("date"):
        try:
            stmt = stmt.where(Candidate.scheduled_date == date.fromisoformat(args["date"]))
        except ValueError:
            pass
    if args.get("gender") in ("M", "F"):
        stmt = stmt.where(Candidate.gender == args["gender"])

    kobo = args.get("kobo")
    if kobo == "validated":
        stmt = stmt.where(Candidate.physical_file_status == FILE_VALIDATED)
    elif kobo == "problem":
        stmt = stmt.where(Candidate.physical_file_status.in_((FILE_MISSING, FILE_NOT_FOUND)))
    elif kobo == "pending":
        stmt = stmt.where(Candidate.physical_file_status == FILE_PENDING)
    elif kobo == "none":
        stmt = stmt.where(Candidate.physical_file_status.is_(None))

    motivation = args.get("motivation")
    if motivation == "done":
        stmt = stmt.where(Candidate.motivation_completed.is_(True))
    elif motivation == "pending":
        stmt = stmt.where(Candidate.motivation_completed.is_(False))

    technical = args.get("technical")
    if technical == "done":
        stmt = stmt.where(Candidate.technical_completed.is_(True))
    elif technical == "running":
        stmt = stmt.where(_active_session_exists())
    elif technical == "waiting":
        stmt = stmt.where(
            Candidate.motivation_completed.is_(True), Candidate.technical_completed.is_(False),
            ~_active_session_exists(),
        )

    column = SORTS.get(args.get("sort"))
    if column is not None:
        ordered = column.desc() if args.get("dir") == "desc" else column.asc()
        stmt = stmt.order_by(ordered.nulls_last(), Candidate.id)
    else:
        stmt = stmt.order_by(
            Candidate.scheduled_date.asc().nulls_last(), Candidate.group_name.asc().nulls_last(),
            Candidate.passage_order.asc().nulls_last(), Candidate.id,
        )
    return stmt


def distinct_groups() -> list[str]:
    return list(db.session.scalars(
        select(Candidate.group_name).where(Candidate.group_name.is_not(None)).distinct().order_by(Candidate.group_name)
    ))


# ---------------------------------------------------------------- exam codes

def normalize_exam_code(value) -> str:
    """Exam codes are case-sensitive: kept exactly as typed, only surrounding spaces are removed."""
    return str(value or "").strip()


def next_exam_code() -> str:
    fmt = settings_service.get("exam_code_format") or "{prefix}-{year}-{seq:04d}"
    params = {"prefix": settings_service.get("exam_code_prefix"), "year": settings_service.get("campaign_year")}
    head = fmt.split("{seq")[0].format(**params)
    pattern = re.compile("^" + re.escape(head) + r"(\d+)")
    existing = db.session.scalars(select(Candidate.exam_code).where(Candidate.exam_code.startswith(head)))
    last = max((int(m.group(1)) for code in existing if (m := pattern.match(code))), default=0)
    return normalize_exam_code(fmt.format(seq=last + 1, **params))


def validate_motivation(candidate: Candidate, piece_number: str, exam_code: str, user: User) -> Candidate:
    """Record piece number + exam code and mark motivation as completed (spec §20-21)."""
    if candidate.motivation_completed and user.role != ROLE_ADMIN:
        raise BusinessError("La motivation de ce candidat est déjà validée. Seul un administrateur peut la modifier.")

    piece_number = (piece_number or "").strip()
    if not piece_number:
        raise BusinessError("Le numéro de pièce est obligatoire.")
    if len(piece_number) > 50:
        raise BusinessError("Le numéro de pièce est trop long (50 caractères max).")

    exam_code = normalize_exam_code(exam_code)
    generated = not exam_code
    if exam_code and not EXAM_CODE_RE.match(exam_code):
        raise BusinessError("Code examen invalide : lettres, chiffres et - _ / . uniquement (50 max).")

    for _attempt in range(5):  # retries only matter for concurrent auto-generation
        code = next_exam_code() if generated else exam_code
        taken = db.session.scalar(select(Candidate.id).where(Candidate.exam_code == code, Candidate.id != candidate.id))
        if taken:
            if generated:
                continue
            raise BusinessError("Ce code examen est déjà attribué à un autre candidat.")

        code_changed = candidate.exam_code != code
        candidate.piece_number = piece_number
        candidate.exam_code = code
        if not candidate.motivation_completed:
            candidate.motivation_completed = True
            candidate.motivation_completed_at = utcnow()
            candidate.motivation_completed_by = user.id
        if code_changed:
            audit_service.log("exam_code_create", candidate, {"exam_code": code, "generated": generated})
        audit_service.log("motivation_validate", candidate, {"piece_number": piece_number, "exam_code": code})
        try:
            db.session.commit()
            return candidate
        except IntegrityError:
            db.session.rollback()
            db.session.refresh(candidate)
            if not generated:
                raise BusinessError("Ce code examen est déjà attribué à un autre candidat.")
    raise BusinessError("Impossible de générer un code examen unique, réessayez.")


# ---------------------------------------------------------------- dashboards

def _count(*conditions) -> int:
    return db.session.scalar(select(func.count(Candidate.id)).where(*conditions)) or 0


def _count_sessions(*statuses) -> int:
    return db.session.scalar(
        select(func.count(TechnicalTestSession.id)).where(TechnicalTestSession.status.in_(statuses))
    ) or 0


def admin_stats() -> dict:
    total = _count()
    motivation_done = _count(Candidate.motivation_completed.is_(True))
    return {
        "total": total,
        "kobo_ok": _count(Candidate.physical_file_status == FILE_VALIDATED),
        "kobo_check": _count(Candidate.physical_file_status == FILE_PENDING),
        "kobo_problem": _count(Candidate.physical_file_status.in_((FILE_MISSING, FILE_NOT_FOUND))),
        "kobo_ambiguous": db.session.scalar(
            select(func.count(KoboSubmission.id)).where(KoboSubmission.match_status == MATCH_AMBIGUOUS)
        ) or 0,
        "motivation_done": motivation_done,
        "motivation_left": total - motivation_done,
        "technical_running": _count_sessions(*ACTIVE_STATUSES),
        "technical_done": _count(Candidate.technical_completed.is_(True)),
    }


def group_summary() -> list:
    def total_if(condition):
        return func.sum(case((condition, 1), else_=0))

    return db.session.execute(
        select(
            func.coalesce(Candidate.group_name, "—").label("group"),
            func.count(Candidate.id).label("total"),
            total_if(Candidate.physical_file_status == FILE_VALIDATED).label("file_ok"),
            total_if(Candidate.motivation_completed.is_(True)).label("motivation"),
            total_if(Candidate.technical_completed.is_(True)).label("technical"),
        ).group_by(Candidate.group_name).order_by(Candidate.group_name)
    ).all()


def motivation_stats(day: date) -> dict:
    on_day = Candidate.scheduled_date == day
    return {
        "today": _count(on_day),
        "waiting": _count(on_day, Candidate.motivation_completed.is_(False)),
        "file_ok": _count(on_day, Candidate.physical_file_status == FILE_VALIDATED),
        "file_problem": _count(on_day, Candidate.physical_file_status.in_((FILE_MISSING, FILE_NOT_FOUND, FILE_PENDING))),
        "done": _count(on_day, Candidate.motivation_completed.is_(True)),
    }


def next_candidates(day: date, limit: int = 15) -> list[Candidate]:
    return list(db.session.scalars(
        select(Candidate)
        .where(Candidate.scheduled_date == day, Candidate.motivation_completed.is_(False))
        .order_by(Candidate.passage_order.asc().nulls_last(), Candidate.id)
        .limit(limit)
    ))


def technical_stats() -> dict:
    from app.services import timer_service

    eligible = (Candidate.motivation_completed.is_(True), Candidate.technical_completed.is_(False))
    occupied = _count_sessions(*ACTIVE_STATUSES)
    return {
        "eligible": _count(*eligible),
        "waiting": _count(*eligible, ~_active_session_exists()),
        "running": _count_sessions(STATUS_RUNNING),
        "paused": _count_sessions(STATUS_PAUSED),
        "done": _count(Candidate.technical_completed.is_(True)),
        "free": timer_service.active_workstation_count() - occupied,
        "occupied": occupied,
    }
