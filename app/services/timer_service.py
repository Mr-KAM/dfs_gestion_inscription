"""Technical test sessions: workstation assignment and server-side chronometer.

The database timestamps are the single source of truth; browsers only render
`remaining_seconds` returned by the server (spec §30).
"""
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from app.extensions import db
from app.models import Candidate, Room, TechnicalTestSession, User, Workstation
from app.models.technical_test import (
    ACTIVE_STATUSES, STATUS_COMPLETED, STATUS_PAUSED, STATUS_RUNNING, STATUS_STOPPED,
)
from app.services import audit_service
from app.services.candidate_service import BusinessError
from app.utils import as_utc, utcnow

OCCUPIED_MESSAGE = "Cet ordinateur est actuellement utilisé par un autre candidat."
MAX_DURATION_MINUTES = 480


def active_workstation_count() -> int:
    return db.session.scalar(
        select(func.count(Workstation.id)).join(Room).where(Workstation.active.is_(True), Room.active.is_(True))
    ) or 0


def occupied_sessions_by_workstation() -> dict[int, TechnicalTestSession]:
    sessions = db.session.scalars(
        select(TechnicalTestSession)
        .options(selectinload(TechnicalTestSession.candidate))
        .where(TechnicalTestSession.status.in_(ACTIVE_STATUSES))
    )
    return {s.workstation_id: s for s in sessions}


def free_workstations(room_id: int) -> list[Workstation]:
    busy = select(TechnicalTestSession.workstation_id).where(TechnicalTestSession.status.in_(ACTIVE_STATUSES))
    return list(db.session.scalars(
        select(Workstation).join(Room)
        .where(Workstation.room_id == room_id, Workstation.active.is_(True), Room.active.is_(True),
               Workstation.id.not_in(busy))
        .order_by(Workstation.computer_number)
    ))


def active_sessions() -> list[TechnicalTestSession]:
    return list(db.session.scalars(
        select(TechnicalTestSession)
        .options(selectinload(TechnicalTestSession.candidate), selectinload(TechnicalTestSession.room),
                 selectinload(TechnicalTestSession.workstation))
        .where(TechnicalTestSession.status.in_(ACTIVE_STATUSES))
        .order_by(TechnicalTestSession.started_at)
    ))


def start_test(candidate_id: int, room_id: int | None, workstation_id: int | None,
               duration_minutes: int | None, tester: User) -> TechnicalTestSession:
    """Assign a workstation and start the chronometer in one transaction."""
    if not room_id:
        raise BusinessError("Sélectionnez une salle.")
    if not workstation_id:
        raise BusinessError("Sélectionnez un ordinateur libre.")
    if not duration_minutes or not 1 <= duration_minutes <= MAX_DURATION_MINUTES:
        raise BusinessError(f"La durée doit être comprise entre 1 et {MAX_DURATION_MINUTES} minutes.")

    try:
        # Row locks serialise concurrent testers on the same PC / candidate (SELECT ... FOR UPDATE).
        workstation = db.session.scalar(select(Workstation).where(Workstation.id == workstation_id).with_for_update())
        candidate = db.session.scalar(select(Candidate).where(Candidate.id == candidate_id).with_for_update())
        if candidate is None:
            raise BusinessError("Candidat introuvable.")
        if not candidate.motivation_completed:
            raise BusinessError("Le test de motivation de ce candidat n'est pas validé.")
        if candidate.technical_completed:
            raise BusinessError("Le test technique de ce candidat est déjà terminé.")
        if workstation is None or workstation.room_id != room_id or not workstation.active or not workstation.room.active:
            raise BusinessError("Ordinateur invalide pour cette salle.")
        if _active_on(TechnicalTestSession.workstation_id == workstation.id):
            raise BusinessError(OCCUPIED_MESSAGE)
        if _active_on(TechnicalTestSession.candidate_id == candidate.id):
            raise BusinessError("Ce candidat a déjà un test technique en cours.")

        session = TechnicalTestSession(
            candidate_id=candidate.id, technical_tester_id=tester.id, room_id=room_id,
            workstation_id=workstation.id, duration_seconds=duration_minutes * 60,
            started_at=utcnow(), total_pause_seconds=0, status=STATUS_RUNNING,
        )
        db.session.add(session)
        db.session.flush()  # the partial unique indexes fire here if we raced
        details = {"candidate": candidate.full_name, "room": workstation.room.name, "workstation": workstation.name}
        audit_service.log("workstation_assign", session, details)
        audit_service.log("test_start", session, {**details, "duration_minutes": duration_minutes})
        db.session.commit()
        return session
    except IntegrityError:
        db.session.rollback()
        raise BusinessError(OCCUPIED_MESSAGE)
    except BusinessError:
        db.session.rollback()
        raise


def _active_on(condition) -> bool:
    return db.session.scalar(
        select(TechnicalTestSession.id).where(condition, TechnicalTestSession.status.in_(ACTIVE_STATUSES))
    ) is not None


def _locked(session_id: int) -> TechnicalTestSession:
    session = db.session.scalar(
        select(TechnicalTestSession).where(TechnicalTestSession.id == session_id).with_for_update()
    )
    if session is None:
        raise BusinessError("Session introuvable.")
    return session


def _close_pause(session: TechnicalTestSession, now) -> None:
    if session.status == STATUS_PAUSED and session.paused_at:
        session.total_pause_seconds += int((now - as_utc(session.paused_at)).total_seconds())
        session.paused_at = None


def pause(session_id: int) -> TechnicalTestSession:
    session = _locked(session_id)
    if session.status != STATUS_RUNNING:
        db.session.rollback()
        raise BusinessError("Seul un test en cours peut être mis en pause.")
    session.paused_at = utcnow()
    session.status = STATUS_PAUSED
    audit_service.log("test_pause", session)
    db.session.commit()
    return session


def resume(session_id: int) -> TechnicalTestSession:
    session = _locked(session_id)
    if session.status != STATUS_PAUSED:
        db.session.rollback()
        raise BusinessError("Seul un test en pause peut être repris.")
    _close_pause(session, utcnow())
    session.status = STATUS_RUNNING
    audit_service.log("test_resume", session, {"total_pause_seconds": session.total_pause_seconds})
    db.session.commit()
    return session


def stop(session_id: int) -> TechnicalTestSession:
    session = _locked(session_id)
    if not session.is_active:
        db.session.rollback()
        raise BusinessError("Ce test n'est pas actif.")
    now = utcnow()
    _close_pause(session, now)
    session.status = STATUS_STOPPED
    session.finished_at = now
    audit_service.log("test_stop", session, {"elapsed_seconds": session.elapsed_seconds(now)})
    db.session.commit()
    return session


def complete(session_id: int) -> TechnicalTestSession:
    session = _locked(session_id)
    if not session.is_active:
        db.session.rollback()
        raise BusinessError("Ce test n'est pas actif.")
    now = utcnow()
    _close_pause(session, now)
    session.status = STATUS_COMPLETED
    session.finished_at = now
    session.candidate.technical_completed = True
    session.candidate.technical_completed_at = now
    audit_service.log("test_complete", session, {"elapsed_seconds": session.elapsed_seconds(now)})
    db.session.commit()
    return session
