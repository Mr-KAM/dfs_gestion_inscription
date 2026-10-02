from flask import Blueprint, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.candidates import get_visible_candidate
from app.extensions import db
from app.models import Room, TechnicalTestSession
from app.models.user import ROLE_ADMIN, ROLE_TECHNICAL
from app.services import candidate_service, timer_service
from app.utils import roles_required

bp = Blueprint("technical", __name__, url_prefix="/technical")
TECH_ROLES = (ROLE_ADMIN, ROLE_TECHNICAL)
ACTIONS = {
    "pause": (timer_service.pause, "Test mis en pause."),
    "resume": (timer_service.resume, "Test repris."),
    "stop": (timer_service.stop, "Test arrêté. L'ordinateur est libéré."),
    "complete": (timer_service.complete, "Test technique terminé. L'ordinateur est libéré."),
}


@bp.get("/sessions")
@roles_required(*TECH_ROLES)
def sessions():
    template = "technical/_sessions.html" if request.args.get("partial") else "technical/sessions.html"
    return render_template(template, sessions=timer_service.active_sessions())


@bp.get("/rooms")
@roles_required(*TECH_ROLES)
def rooms():
    stmt = select(Room).options(selectinload(Room.workstations)).where(Room.active.is_(True)).order_by(Room.name)
    all_rooms = db.session.scalars(stmt).all()
    room_id = request.args.get("room", type=int)
    shown = [r for r in all_rooms if not room_id or r.id == room_id]
    ctx = {"rooms": shown, "all_rooms": all_rooms, "room_id": room_id,
           "occupied": timer_service.occupied_sessions_by_workstation()}
    return render_template("technical/_rooms.html" if request.args.get("partial") else "technical/rooms.html", **ctx)


@bp.get("/rooms/<int:room_id>/free.json")
@roles_required(*TECH_ROLES)
def free_workstations(room_id: int):
    return jsonify([{"id": w.id, "name": w.name} for w in timer_service.free_workstations(room_id)])


@bp.get("/candidate/<int:candidate_id>/panel")
@roles_required(*TECH_ROLES)
def panel(candidate_id: int):
    """Polled fragment: live chronometer + controls for the candidate's session."""
    return render_template("technical/_panel.html", c=get_visible_candidate(candidate_id))


@bp.post("/start/<int:candidate_id>")
@roles_required(*TECH_ROLES)
def start(candidate_id: int):
    get_visible_candidate(candidate_id)
    try:
        session = timer_service.start_test(
            candidate_id, request.form.get("room_id", type=int), request.form.get("workstation_id", type=int),
            request.form.get("duration_minutes", type=int), current_user._get_current_object(),
        )
        flash(f"Test lancé sur {session.workstation.name} ({session.room.name}).", "success")
    except candidate_service.BusinessError as exc:
        flash(str(exc), "danger")
    return redirect(url_for("candidates.detail", candidate_id=candidate_id))


@bp.post("/session/<int:session_id>/<action>")
@roles_required(*TECH_ROLES)
def control(session_id: int, action: str):
    if action not in ACTIONS:
        return redirect(url_for("technical.sessions"))
    session = db.get_or_404(TechnicalTestSession, session_id)
    func, message = ACTIONS[action]
    try:
        func(session.id)
        flash(message, "success")
    except candidate_service.BusinessError as exc:
        flash(str(exc), "danger")
    return redirect(url_for("candidates.detail", candidate_id=session.candidate_id))
