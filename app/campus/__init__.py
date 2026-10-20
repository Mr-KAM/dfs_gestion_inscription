"""SAS campus life, for the admin and the campus staff:
coach (roll calls, notifications, exercises for his classes), reception (visits, absences, problems),
residence manager (bedrooms / equipment: the /admin/sas pages)."""
from flask import Blueprint, abort, flash, make_response, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy import select

from app.extensions import db
from app.models import AttendanceSession, Candidate, Exercise, Incident, Notification, Sas, Visit
from app.models.attendance import METHOD_LABELS
from app.models.campus import INCIDENT_CATEGORIES, REJECTED, VALIDATED
from app.models.user import CAMPUS_ROLES, ROLE_ADMIN, ROLE_RECEPTION
from app.services import attendance_service, audit_service, campus_service
from app.services.candidate_service import BusinessError
from app.utils import allow_camera, roles_required, utcnow

bp = Blueprint("campus", __name__, url_prefix="/campus")
DESK_ROLES = (ROLE_ADMIN, ROLE_RECEPTION)  # visits, every incident


@bp.before_request
@roles_required(*CAMPUS_ROLES)
def require_campus_staff():
    """Every /campus route: admin or campus staff; finer rights are checked per class below."""


def _require_class(sas_id: int, class_name: str) -> None:
    if not campus_service.can_manage(current_user, sas_id, class_name):
        abort(403)


def _require_desk() -> None:
    if current_user.role not in DESK_ROLES:
        abort(403)


@bp.get("/")
def index():
    classes = [c for c in campus_service.class_choices()
               if campus_service.can_manage(current_user, c["sas"].id, c["class_name"])]
    sessions = [s for s in db.session.scalars(select(AttendanceSession).order_by(AttendanceSession.opened_at.desc())
                                              .limit(100)) if campus_service.can_manage(current_user, s.sas_id, s.class_name)]
    return render_template("campus/index.html", classes=classes, sessions=sessions[:30],
                           targets=campus_service.targets(current_user))


# ------------------------------------------------------------------ roll call (pointage)

@bp.post("/attendance")
def attendance_open():
    try:
        sas, class_name = campus_service.parse_target(current_user, request.form.get("target", ""))
        session = attendance_service.open_session(sas, class_name, request.form.get("label", ""),
                                                  request.form.get("minutes", 0, type=int) or 0, current_user)
    except BusinessError as exc:
        flash(str(exc), "danger")
        return redirect(request.referrer or url_for("campus.index"))
    return redirect(url_for("campus.attendance", session_id=session.id))


def _session(session_id: int) -> AttendanceSession:
    session = db.get_or_404(AttendanceSession, session_id)
    _require_class(session.sas_id, session.class_name)
    return session


def _attendance_context(session: AttendanceSession) -> dict:
    students = attendance_service.roster(session)
    marks = {m.candidate_id: m for m in session.marks}
    return {"s": session, "students": students, "marks": marks, "present": sum(1 for c in students if c.id in marks),
            "method_labels": METHOD_LABELS, "qr": attendance_service.qr_svg(session) if session.is_open else None}


@bp.get("/attendance/<int:session_id>")
def attendance(session_id: int):
    session = _session(session_id)
    if request.args.get("partial"):
        return render_template("campus/_attendance_live.html", **_attendance_context(session))
    return allow_camera(make_response(render_template("campus/attendance.html", **_attendance_context(session))))


@bp.post("/attendance/<int:session_id>/scan")
def attendance_scan(session_id: int):
    """Staff scans the student's matricule QR code (works open or closed)."""
    session = _session(session_id)
    try:
        candidate, new = attendance_service.admin_scan(session, request.form.get("token", ""), current_user)
        flash(f"{candidate.full_name} : présent(e)." if new else f"{candidate.full_name} était déjà marqué(e) présent(e).",
              "success" if new else "info")
    except BusinessError as exc:
        flash(str(exc), "danger")
    return redirect(url_for("campus.attendance", session_id=session.id))


@bp.post("/attendance/<int:session_id>/mark/<int:candidate_id>")
def attendance_mark(session_id: int, candidate_id: int):
    session = _session(session_id)
    try:
        attendance_service.admin_set(session, db.get_or_404(Candidate, candidate_id),
                                     request.form.get("present") == "1", current_user)
    except BusinessError as exc:
        flash(str(exc), "danger")
    return redirect(url_for("campus.attendance", session_id=session.id))


@bp.post("/attendance/<int:session_id>/close")
def attendance_close(session_id: int):
    session = _session(session_id)
    attendance_service.close_session(session)
    flash("Pointage clôturé : les étudiants ne peuvent plus marquer leur présence.", "success")
    return redirect(url_for("campus.attendance", session_id=session.id))


# ------------------------------------------------------------------ notifications

@bp.route("/notifications", methods=["GET", "POST"])
def notifications():
    if request.method == "POST":
        try:
            campus_service.send_notification(current_user, request.form.get("target", ""),
                                             request.form.get("title", ""), request.form.get("body", ""))
            flash("Notification envoyée.", "success")
            return redirect(url_for("campus.notifications"))
        except BusinessError as exc:
            flash(str(exc), "danger")
    sent = db.session.scalars(campus_service.visible_query(Notification, current_user)
                              .order_by(Notification.created_at.desc()).limit(100)).all()
    return render_template("campus/notifications.html", sent=sent, targets=campus_service.targets(current_user))


# ------------------------------------------------------------------ exercises

@bp.route("/exercises", methods=["GET", "POST"])
def exercises():
    if request.method == "POST":
        try:
            exercise = campus_service.create_exercise(current_user, request.form)
            flash("Exercice publié." if exercise.is_published else "Exercice programmé.", "success")
            return redirect(url_for("campus.exercise", exercise_id=exercise.id))
        except BusinessError as exc:
            flash(str(exc), "danger")
    items = db.session.scalars(campus_service.visible_query(Exercise, current_user)
                               .order_by(Exercise.opens_at.desc()).limit(200)).all()
    return render_template("campus/exercises.html", exercises=items, targets=campus_service.targets(current_user))


def _exercise(exercise_id: int) -> Exercise:
    exercise = db.get_or_404(Exercise, exercise_id)
    _require_class(exercise.sas_id, exercise.class_name)
    return exercise


@bp.get("/exercises/<int:exercise_id>")
def exercise(exercise_id: int):
    exercise = _exercise(exercise_id)
    submissions = {s.candidate_id: s for s in exercise.submissions}
    return render_template("campus/exercise.html", e=exercise, students=campus_service.exercise_roster(exercise),
                           submissions=submissions, VALIDATED=VALIDATED, REJECTED=REJECTED)


@bp.post("/exercises/<int:exercise_id>/review/<int:candidate_id>")
def exercise_review(exercise_id: int, candidate_id: int):
    exercise = _exercise(exercise_id)
    try:
        campus_service.review(exercise, db.get_or_404(Candidate, candidate_id), current_user,
                              request.form.get("status", ""), request.form.get("score", ""), request.form.get("feedback", ""))
    except BusinessError as exc:
        flash(str(exc), "danger")
    return redirect(url_for("campus.exercise", exercise_id=exercise.id) + f"#s{candidate_id}")


# ------------------------------------------------------------------ reception: visits, problems

@bp.route("/visits", methods=["GET", "POST"])
def visits():
    _require_desk()
    if request.method == "POST":
        try:
            campus_service.add_visit(current_user, request.form)
            flash("Visite enregistrée.", "success")
            return redirect(url_for("campus.visits"))
        except BusinessError as exc:
            flash(str(exc), "danger")
    items = db.session.scalars(select(Visit).order_by(Visit.left_at.is_not(None), Visit.arrived_at.desc()).limit(200)).all()
    return render_template("campus/visits.html", visits=items, students=campus_service.sas_students())


@bp.post("/visits/<int:visit_id>/leave")
def visit_leave(visit_id: int):
    _require_desk()
    visit = db.get_or_404(Visit, visit_id)
    visit.left_at = visit.left_at or utcnow()
    audit_service.log("visit_leave", visit)
    db.session.commit()
    return redirect(url_for("campus.visits"))


@bp.route("/incidents", methods=["GET", "POST"])
def incidents():
    """Everyone on campus can report a problem; the reception (and admin) sees and resolves them all."""
    if request.method == "POST":
        try:
            campus_service.add_incident(current_user, request.form)
            flash("Problème signalé.", "success")
            return redirect(url_for("campus.incidents"))
        except BusinessError as exc:
            flash(str(exc), "danger")
    status = request.args.get("status", "open")
    stmt = select(Incident).order_by(Incident.created_at.desc()).limit(300)
    if current_user.role not in DESK_ROLES:
        stmt = stmt.where(Incident.created_by == current_user.id)  # own reports only (health, discipline: private)
    if status == "open":
        stmt = stmt.where(Incident.resolved_at.is_(None))
    elif status == "resolved":
        stmt = stmt.where(Incident.resolved_at.is_not(None))
    return render_template("campus/incidents.html", incidents=db.session.scalars(stmt).all(), status=status,
                           categories=INCIDENT_CATEGORIES, students=campus_service.sas_students(),
                           can_resolve=current_user.role in DESK_ROLES)


@bp.post("/incidents/<int:incident_id>/resolve")
def incident_resolve(incident_id: int):
    _require_desk()
    incident = db.get_or_404(Incident, incident_id)
    incident.resolution = request.form.get("resolution", "").strip()[:5000]
    incident.resolved_at, incident.resolved_by = utcnow(), current_user.id
    audit_service.log("incident_resolve", incident)
    db.session.commit()
    flash("Problème marqué comme résolu.", "success")
    return redirect(url_for("campus.incidents"))
