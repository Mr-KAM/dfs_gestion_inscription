"""Candidate queries, filters, motivation validation and exam codes."""
import base64
import hashlib
import hmac
import re
import secrets
import string
from datetime import date, datetime
from zoneinfo import ZoneInfo

import segno
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from flask import current_app
from markupsafe import Markup
from sqlalchemy import case, event, exists, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

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


# ---------------------------------------------------------------- matricule

def matricule_prefix() -> str:
    return f"{today():%y}CAND"  # 2026 -> "26CAND"


@event.listens_for(Session, "before_flush")
def _assign_matricules(session, _context, _instances) -> None:
    """Every new candidate, whatever the path (form, import, sign-up), gets 26CAND000001, 26CAND000002...
    in insertion order (session.new keeps it)."""
    new = [obj for obj in session.new if isinstance(obj, Candidate) and not obj.matricule]
    if not new:
        return
    prefix = matricule_prefix()
    # ponytail: max+1, two simultaneous sign-ups can collide -> the UNIQUE constraint rejects the second one.
    last = session.scalar(select(func.max(Candidate.matricule)).where(Candidate.matricule.like(f"{prefix}%")))
    number = int(last[len(prefix):]) if last else 0
    for candidate in new:
        number += 1
        candidate.matricule = f"{prefix}{number:06d}"


MATRICULE_RE = re.compile(r"^\d{2}CAND\d{6}$")
QR_NOISE = string.ascii_letters + string.digits
QR_TOKEN_MAX = 200


QR_UNREADABLE = "QR code illisible : ce n'est pas un QR code matricule DFS."
QR_FORGED = "QR code refusé : signature invalide (QR code modifié ou non émis par cette application)."
QR_NONCE = 12  # AES-GCM nonce bytes
QR_RAW_LEN = QR_NONCE + 22 + 16  # nonce + encrypted 22 bytes + GCM tag = 50 bytes


def _qr_cipher() -> AESGCM:
    """AES-256-GCM, key derived from SECRET_KEY for this purpose only (not shared with encrypt_secret)."""
    key = hmac.new(current_app.config["SECRET_KEY"].encode(), b"dfs-matricule-qr-aesgcm-v1", hashlib.sha256).digest()
    return AESGCM(key)


def encode_matricule(matricule: str) -> str:
    """QR content: base32(nonce + AES-256-GCM(binary)) -> 80 chars, QR version 4.
    binary: 5 random chars + matricule + 5 random chars as raw bytes (8 bits per char). Not as '0'/'1'
    text: that made the code 8x longer and too dense (version 12) for cameras to read.
    base32 (A-Z, 2-7) fits the QR alphanumeric mode, denser than base64.
    Encrypted: nothing readable without the server key. The GCM tag is the signature: any modified
    or home-made QR code fails decryption. Changing SECRET_KEY invalidates printed QR codes."""
    noise = lambda: "".join(secrets.choice(QR_NOISE) for _ in range(5))  # noqa: E731
    binary = (noise() + matricule + noise()).encode("ascii")
    nonce = secrets.token_bytes(QR_NONCE)
    sealed = _qr_cipher().encrypt(nonce, binary, None)
    return base64.b32encode(nonce + sealed).decode("ascii").rstrip("=")


def decode_matricule(token: str) -> str:
    """Admin scanner: base32 -> decrypt + verify the tag -> binary -> matricule.
    Raises BusinessError on anything that is not a QR code issued by this application."""
    token = (token or "").strip().upper()  # some handheld scanners type lowercase
    try:
        if not token or len(token) > QR_TOKEN_MAX or not token.isascii():
            raise ValueError
        raw = base64.b32decode(token + "=" * (-len(token) % 8))
        if len(raw) != QR_RAW_LEN:
            raise ValueError
    except ValueError:  # binascii.Error is a ValueError
        raise BusinessError(QR_UNREADABLE)
    try:
        binary = _qr_cipher().decrypt(raw[:QR_NONCE], raw[QR_NONCE:], None)
    except InvalidTag:
        raise BusinessError(QR_FORGED)
    matricule = binary[5:-5].decode("ascii", "replace")
    if not MATRICULE_RE.match(matricule):
        raise BusinessError(QR_UNREADABLE)
    return matricule


def matricule_qr(candidate: Candidate) -> Markup:
    """Inline SVG QR code (new random noise on every render), used as a Jinja global.
    border=4: the quiet zone required by the QR spec, cameras miss codes without it.
    omitsize: viewBox instead of fixed width/height, so the CSS width SCALES the code (with a fixed
    size and no viewBox the browser crops it: right/bottom edge cut off, unreadable)."""
    svg = segno.make(encode_matricule(candidate.matricule), error="m").svg_inline(
        border=4, dark="#000", light="#fff", omitsize=True)
    return Markup(svg)


def _active_session_exists():
    return exists().where(
        TechnicalTestSession.candidate_id == Candidate.id, TechnicalTestSession.status.in_(ACTIVE_STATUSES)
    )


SORTS = {
    "matricule": Candidate.matricule,
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
            Candidate.candidate_number.ilike(like), Candidate.matricule.ilike(like),
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
