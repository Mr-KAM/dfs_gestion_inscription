"""SAS check-in: the admin opens a period for a class; students mark themselves present by scanning the
rotating QR code shown by the admin, or the admin scans their matricule QR. After closing, only the admin can mark."""
import hashlib
import hmac
from datetime import timedelta
from urllib.parse import parse_qs, urlsplit

import segno
from flask import current_app, url_for
from markupsafe import Markup
from sqlalchemy import or_, select

from app.extensions import db
from app.models import Attendance, AttendanceSession, Candidate
from app.models.attendance import METHOD_ADMIN, METHOD_SCAN, METHOD_SELF
from app.services import audit_service, candidate_service
from app.services.candidate_service import BusinessError
from app.utils import as_utc, utcnow

CODE_WINDOW = 60  # seconds: the QR code shown by the admin changes every minute (a shared photo soon expires)
MAX_DURATION_MINUTES = 12 * 60


def open_session(sas, class_name: str, label: str, minutes: int, user) -> AttendanceSession:
    if not 1 <= minutes <= MAX_DURATION_MINUTES:
        raise BusinessError(f"Durée invalide : entre 1 et {MAX_DURATION_MINUTES} minutes.")
    now = utcnow()
    session = AttendanceSession(sas=sas, class_name=class_name[:50], label=(label.strip() or "Pointage")[:100],
                                opened_at=now, closes_at=now + timedelta(minutes=minutes), created_by=user.id)
    db.session.add(session)
    db.session.flush()
    audit_service.log("attendance_open", session, {"class": session.class_name, "minutes": minutes})
    db.session.commit()
    return session


def close_session(session: AttendanceSession) -> None:
    session.closes_at = min(as_utc(session.closes_at), utcnow())
    audit_service.log("attendance_close", session)
    db.session.commit()


def roster(session: AttendanceSession) -> list[Candidate]:
    """Live: the students currently in that SAS / class."""
    stmt = select(Candidate).where(Candidate.sas_id == session.sas_id).order_by(Candidate.sas_class, Candidate.full_name)
    if session.class_name:
        stmt = stmt.where(Candidate.sas_class == session.class_name)
    return db.session.scalars(stmt).all()


def in_session(candidate: Candidate, session: AttendanceSession) -> bool:
    return candidate.sas_id == session.sas_id and session.class_name in ("", candidate.sas_class)


def sessions_for(candidate: Candidate) -> list[AttendanceSession]:
    if not candidate.sas_id:
        return []
    return db.session.scalars(select(AttendanceSession).where(
        AttendanceSession.sas_id == candidate.sas_id,
        or_(AttendanceSession.class_name == "", AttendanceSession.class_name == (candidate.sas_class or "")),
    ).order_by(AttendanceSession.opened_at.desc())).all()


def marks_by_session(candidate: Candidate) -> dict[int, Attendance]:
    return {m.session_id: m for m in db.session.scalars(select(Attendance).where(Attendance.candidate_id == candidate.id))}


# ---- rotating code shown by the admin (QR = portal URL carrying the code)

def _sign(session_id: int, window: int) -> str:
    key = current_app.config["SECRET_KEY"].encode()
    return hmac.new(key, f"attendance:{session_id}:{window}".encode(), hashlib.sha256).hexdigest()[:16]


def current_code(session: AttendanceSession) -> str:
    window = int(utcnow().timestamp()) // CODE_WINDOW
    return f"{session.id}-{window}-{_sign(session.id, window)}"


def qr_svg(session: AttendanceSession) -> Markup:
    url = url_for("portal.attendance", code=current_code(session), _external=True)
    return Markup(segno.make(url, error="m").svg_inline(border=4, dark="#000", light="#fff", omitsize=True))


def session_from_code(text: str) -> AttendanceSession:
    """Accepts the bare code or the scanned URL. Valid for the current and the previous minute."""
    text = (text or "").strip()[:500]
    if "code=" in text:
        text = (parse_qs(urlsplit(text).query).get("code") or [""])[0]
    try:
        session_id, window, sig = text.split("-")
        session_id, window = int(session_id), int(window)
    except ValueError:
        raise BusinessError("QR code de pointage illisible.")
    if not hmac.compare_digest(sig, _sign(session_id, window)):
        raise BusinessError("QR code de pointage invalide.")
    if int(utcnow().timestamp()) // CODE_WINDOW - window not in (0, 1):
        raise BusinessError("QR code expiré : scannez le QR code actuellement affiché.")
    session = db.session.get(AttendanceSession, session_id)
    if session is None:
        raise BusinessError("Ce pointage n'existe plus.")
    return session


# ---- marking

def _mark(session: AttendanceSession, candidate: Candidate, method: str, user) -> bool:
    """True if newly marked, False if already present."""
    if any(m.candidate_id == candidate.id for m in session.marks):
        return False
    session.marks.append(Attendance(candidate=candidate, method=method, marked_by=user.id))
    audit_service.log("attendance_mark", session, {"matricule": candidate.matricule, "method": method})
    db.session.commit()
    return True


def self_mark(candidate: Candidate, code: str, user) -> tuple[AttendanceSession, bool]:
    session = session_from_code(code)
    if not in_session(candidate, session):
        raise BusinessError("Ce pointage ne concerne pas votre classe.")
    if not session.is_open:
        raise BusinessError("Le temps de pointage est terminé : seul l'administrateur peut encore marquer votre présence.")
    return session, _mark(session, candidate, METHOD_SELF, user)


def admin_scan(session: AttendanceSession, token: str, user) -> tuple[Candidate, bool]:
    matricule = candidate_service.decode_matricule(token)
    candidate = db.session.scalar(select(Candidate).where(Candidate.matricule == matricule))
    if candidate is None or not in_session(candidate, session):
        raise BusinessError(f"{matricule} : cet étudiant ne fait pas partie de ce pointage.")
    return candidate, _mark(session, candidate, METHOD_SCAN, user)


def admin_set(session: AttendanceSession, candidate: Candidate, present: bool, user) -> None:
    """Admin correction, open or closed: present (manual mark) or absent (mark removed)."""
    if not in_session(candidate, session):
        raise BusinessError("Cet étudiant ne fait pas partie de ce pointage.")
    if present:
        _mark(session, candidate, METHOD_ADMIN, user)
        return
    for mark in [m for m in session.marks if m.candidate_id == candidate.id]:
        session.marks.remove(mark)
        audit_service.log("attendance_mark", session, {"matricule": candidate.matricule, "method": "absent"})
    db.session.commit()
