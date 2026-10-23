"""SAS campus life, for the admin and the campus staff:
coach (roll calls, notifications, exercises for his classes), reception (visits, absences, problems),
residence manager (bedrooms / equipment: the /admin/sas pages)."""
import segno
from flask import Blueprint, abort, flash, jsonify, make_response, redirect, render_template, request, url_for
from flask_login import current_user
from markupsafe import Markup
from sqlalchemy import and_, false, or_, select

from app.extensions import db
from app.models import AttendanceSession, Candidate, Exercise, ExerciseSubmission, Incident, Notification, Sas, SasClass, Visit
from app.models.attendance import HOURS_CHOICES, METHOD_LABELS
from app.models.campus import ACTIVITY_CATEGORIES, ID_TYPES, INCIDENT_CATEGORIES, REJECTED, VALIDATED, VISIT_KINDS
from app.models.user import CAMPUS_ROLES, ROLE_ADMIN, ROLE_COACH, ROLE_RECEPTION
from app.services import attendance_service, audit_service, campus_service
from app.services.candidate_service import BusinessError
from app.utils import allow_camera, roles_required, utcnow

bp = Blueprint("campus", __name__, url_prefix="/campus")
DESK_ROLES = (ROLE_ADMIN, ROLE_RECEPTION)  # site register, every incident
CLASS_ROLES = (ROLE_ADMIN, ROLE_COACH)  # create classes, join QR codes, invitations
EXERCISE_ROLES = (ROLE_ADMIN, ROLE_COACH, ROLE_RECEPTION)  # may write exercise statements (and upload their images)


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


def _require_students_role() -> None:
    if current_user.role not in DESK_ROLES + (ROLE_COACH,):
        abort(403)


def _coach_students():
    """SQL condition: students of the current coach's classes ('sas_id:class' keys)."""
    keys = [k.partition(":") for k in current_user.coach_classes or []]
    conditions = [and_(Candidate.sas_id == int(s), Candidate.sas_class == n) for s, _, n in keys if s.isdigit() and n]
    return or_(*conditions) if conditions else false()


def _require_class_role() -> None:
    if current_user.role not in CLASS_ROLES:
        abort(403)


@bp.get("/")
def index():
    classes = [c for c in campus_service.class_choices()
               if campus_service.can_manage(current_user, c["sas"].id, c["class_name"])]
    recent = db.session.scalars(select(AttendanceSession).order_by(AttendanceSession.opened_at.desc()).limit(100))
    sessions = [s for s in recent if campus_service.can_manage(current_user, s.sas_id, s.class_name)]
    return render_template("campus/index.html", classes=classes, sessions=sessions[:30],
                           targets=campus_service.targets(current_user), **attendance_form_context())


def attendance_form_context() -> dict:
    """Defaults of the shared roll-call form (campus/_attendance_form.html)."""
    return {"now_input": campus_service.local_now_input(), "hours_choices": HOURS_CHOICES}


# ------------------------------------------------------------------ roll call (pointage)

@bp.post("/attendance")
def attendance_open():
    try:
        sas, class_name = campus_service.parse_target(current_user, request.form.get("target", ""))
        session = attendance_service.open_session(
            sas, class_name, request.form.get("label", ""), request.form.get("minutes", 0, type=int) or 0,
            current_user, starts_at=campus_service.local_datetime(request.form.get("starts_at", "")),
            hours=request.form.get("hours", 1, type=int))
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
        flash(f"{candidate.full_name} : présent(e)." if new
              else f"{candidate.full_name} était déjà marqué(e) présent(e).", "success" if new else "info")
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
    everyone = campus_service.exercise_roster(exercise)
    filters = _exercise_filters()
    students = everyone
    if filters.get("q"):
        q = filters["q"].lower()
        students = [c for c in students if q in " ".join((c.full_name or "", c.matricule or "", c.email or "")).lower()]
    if filters.get("status"):  # "none" = no answer / no review yet
        students = [c for c in students
                    if (submissions[c.id].status if c.id in submissions else "none") == filters["status"]]
    if filters.get("class"):
        students = [c for c in students if (c.sas_class or "") == filters["class"]]
    classes = sorted({c.sas_class for c in everyone if c.sas_class})
    return render_template("campus/exercise.html", e=exercise, students=students, total=len(everyone),
                           classes=classes, filters=filters, submissions=submissions,
                           VALIDATED=VALIDATED, REJECTED=REJECTED)


def _exercise_filters() -> dict:
    """Results filters of an exercise page, kept through the review / resubmission forms."""
    return {k: v.strip() for k in ("q", "status", "class") if (v := request.args.get(k, "")).strip()}


def _back_to_exercise(exercise, candidate_id: int):
    return redirect(url_for("campus.exercise", exercise_id=exercise.id, **_exercise_filters()) + f"#s{candidate_id}")


@bp.post("/exercises/<int:exercise_id>/edit")
def exercise_edit(exercise_id: int):
    exercise = _exercise(exercise_id)
    try:
        campus_service.update_exercise(exercise, current_user, request.form)
        flash("Exercice mis à jour.", "success")
    except BusinessError as exc:
        db.session.rollback()
        flash(str(exc), "danger")
    return redirect(url_for("campus.exercise", exercise_id=exercise.id))


@bp.post("/exercises/<int:exercise_id>/resubmit/<int:candidate_id>")
def exercise_resubmit(exercise_id: int, candidate_id: int):
    """Allow (or withdraw) one more submission for this student; previous answers stay in the history."""
    exercise = _exercise(exercise_id)
    allowed = request.form.get("allowed") == "1"
    try:
        campus_service.set_resubmission(exercise, db.get_or_404(Candidate, candidate_id), current_user, allowed)
        flash("Nouvelle soumission autorisée." if allowed else "Autorisation retirée.", "success")
    except BusinessError as exc:
        flash(str(exc), "danger")
    return _back_to_exercise(exercise, candidate_id)


@bp.post("/media")
def media_upload():
    """Image pasted / inserted in the rich editor (fetch from static/js/editor.js, CSRF in the X-CSRFToken header)."""
    if current_user.role not in EXERCISE_ROLES:
        abort(403)
    try:
        media = campus_service.save_media(current_user, request.files.get("file"))
    except BusinessError as exc:
        return jsonify(error=str(exc)), 400
    return jsonify(url=url_for("auth.media", key=media.key))


@bp.post("/exercises/<int:exercise_id>/review/<int:candidate_id>")
def exercise_review(exercise_id: int, candidate_id: int):
    exercise = _exercise(exercise_id)
    try:
        form = request.form
        campus_service.review(exercise, db.get_or_404(Candidate, candidate_id), current_user,
                              form.get("status", ""), form.get("score", ""), form.get("feedback", ""))
    except BusinessError as exc:
        flash(str(exc), "danger")
    return _back_to_exercise(exercise, candidate_id)


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
    stmt = select(Visit).order_by(Visit.left_at.is_not(None), Visit.arrived_at.desc()).limit(200)  # still here first
    items = db.session.scalars(stmt).all()
    return render_template("campus/visits.html", visits=items, students=campus_service.sas_students(sas_only=False),
                           kinds=VISIT_KINDS, id_types=ID_TYPES, categories=ACTIVITY_CATEGORIES)


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


# ------------------------------------------------------------------ classes: creation, join QR code, invitations

@bp.route("/classes", methods=["GET", "POST"])
def classes():
    _require_class_role()
    if request.method == "POST":
        try:
            klass = campus_service.create_class(current_user, request.form.get("sas_id", 0, type=int),
                                                request.form.get("name", ""))
            flash(f"Classe « {klass.name} » créée : partagez son QR code ou invitez des étudiants.", "success")
            return redirect(url_for("campus.class_page", class_id=klass.id))
        except BusinessError as exc:
            flash(str(exc), "danger")
    coaches = campus_service.coaches_by_class()
    mine = [c for c in campus_service.class_choices()
            if campus_service.can_manage(current_user, c["sas"].id, c["class_name"])]
    coach_id = request.args.get("coach", "")
    if coach_id == "none":
        mine = [c for c in mine if not coaches.get(c["key"])]
    elif coach_id.isdigit():
        mine = [c for c in mine if any(u.id == int(coach_id) for u in coaches.get(c["key"], []))]
    exercises, calls = campus_service.class_counts()
    sas_list = db.session.scalars(select(Sas).order_by(Sas.created_at.desc())).all()
    all_coaches = sorted({u.id: u for users in coaches.values() for u in users}.values(), key=lambda u: u.display_name)
    return render_template("campus/classes.html", classes=mine, sas_list=sas_list, coaches=coaches, coach_id=coach_id,
                           all_coaches=all_coaches, exercises=exercises, calls=calls)


@bp.get("/classes/open")
def class_open():
    """From the class list: classes assigned by import get their row (join code) on first opening."""
    _require_class_role()
    sas_id, _, name = request.args.get("key", "").partition(":")
    if not sas_id.isdigit() or not name:
        abort(404)
    _require_class(int(sas_id), name)
    db.get_or_404(Sas, int(sas_id))
    return redirect(url_for("campus.class_page", class_id=campus_service.get_class(int(sas_id), name, current_user).id))


def _klass(class_id: int) -> SasClass:
    _require_class_role()
    klass = db.get_or_404(SasClass, class_id)
    _require_class(klass.sas_id, klass.name)
    return klass


@bp.get("/classes/<int:class_id>")
def class_page(class_id: int):
    klass = _klass(class_id)
    join_url = url_for("portal.join_class", code=klass.join_code, _external=True)
    qr = Markup(segno.make(join_url, error="m").svg_inline(border=4, dark="#000", light="#fff", omitsize=True))
    report = campus_service.class_report(klass)
    # roster filters: search (name / matricule / email), invitation state, validated exercises
    q, state, done = (request.args.get(k, "").strip() for k in ("q", "state", "done"))
    total = len(report["exercises"])
    rows = report["roster"]
    if q:
        rows = [r for r in rows if q.lower() in " ".join(
            (r["candidate"].full_name or "", r["candidate"].matricule or "", r["candidate"].email or "")).lower()]
    if state:
        rows = [r for r in rows if r["state"] == state]
    if done == "none":
        rows = [r for r in rows if not r["validated"]]
    elif done == "some":
        rows = [r for r in rows if 0 < r["validated"] < total]
    elif done == "all":
        rows = [r for r in rows if total and r["validated"] == total]
    report["roster_total"] = len(report["roster"])
    report["roster"] = rows
    return render_template("campus/class.html", k=klass, join_url=join_url, qr=qr,
                           filters={"q": q, "state": state, "done": done}, **report)


@bp.post("/classes/<int:class_id>/invite")
def class_invite(class_id: int):
    klass = _klass(class_id)
    invited, problems = campus_service.invite(klass, current_user, request.form.get("candidates", ""))
    flash(f"{invited} invitation(s) envoyée(s) : les étudiants la voient sur leur espace.", "success")
    if problems:
        flash("Non invité(s) : " + " · ".join(problems[:20]) + (" …" if len(problems) > 20 else ""), "warning")
    return redirect(url_for("campus.class_page", class_id=klass.id))


@bp.post("/classes/<int:class_id>/renew")
def class_renew(class_id: int):
    campus_service.renew_join_code(_klass(class_id))
    flash("Nouveau code : l'ancien QR code et l'ancienne adresse ne fonctionnent plus.", "success")
    return redirect(url_for("campus.class_page", class_id=class_id))


# ------------------------------------------------------------------ reception: SAS students (read only)

@bp.get("/students")
def students():
    """Admitted SAS students: search + filters, read only. A coach only sees the students of his classes."""
    _require_students_role()
    args = {k: request.args.get(k, "").strip() for k in ("q", "sas", "class", "gender")}
    stmt = select(Candidate).where(Candidate.sas_id.is_not(None)).order_by(Candidate.full_name)
    if current_user.role == ROLE_COACH:
        stmt = stmt.where(_coach_students())
    if args["q"]:
        like = f"%{args['q']}%"
        stmt = stmt.where(or_(Candidate.full_name.ilike(like), Candidate.matricule.ilike(like),
                              Candidate.email.ilike(like), Candidate.phone.ilike(like)))
    if args["sas"].isdigit():
        stmt = stmt.where(Candidate.sas_id == int(args["sas"]))
    if args["class"] == "none":
        stmt = stmt.where(or_(Candidate.sas_class.is_(None), Candidate.sas_class == ""))
    elif args["class"]:
        stmt = stmt.where(Candidate.sas_class == args["class"])
    if args["gender"] in ("M", "F"):
        stmt = stmt.where(Candidate.gender == args["gender"])
    page = db.paginate(stmt, page=request.args.get("page", 1, type=int), per_page=50, error_out=False)
    classes = sorted({c["class_name"] for c in campus_service.class_choices()
                      if campus_service.can_manage(current_user, c["sas"].id, c["class_name"])})
    return render_template("campus/students.html", page=page, args=args, classes=classes,
                           sas_list=db.session.scalars(select(Sas).order_by(Sas.name)).all())


@bp.get("/students/<int:candidate_id>")
def student(candidate_id: int):
    _require_students_role()
    c = db.get_or_404(Candidate, candidate_id)
    if not c.sas_id:
        abort(404)  # recruitment candidates stay on the recruitment pages
    _require_class(c.sas_id, c.sas_class or "")  # coach: a student of one of his classes
    desk = current_user.role in DESK_ROLES
    sessions = attendance_service.sessions_for(c)
    marks = attendance_service.marks_by_session(c)
    visits = db.session.scalars(select(Visit).where(Visit.candidate_id == c.id).order_by(Visit.arrived_at.desc())
                                .limit(50)).all() if desk else None  # site register: reception and admin only
    submissions = db.session.scalars(select(ExerciseSubmission).join(Exercise)
                                     .where(ExerciseSubmission.candidate_id == c.id)
                                     .order_by(Exercise.opens_at.desc())).all()
    incidents = db.session.scalars(select(Incident).where(Incident.candidate_id == c.id)
                                   .order_by(Incident.created_at.desc())).all()
    return render_template("campus/student.html", c=c, sessions=sessions[:30], marks=marks, visits=visits,
                           incidents=incidents, submissions=submissions, absence_hours=attendance_service.absence_hours(c, sessions, marks))
