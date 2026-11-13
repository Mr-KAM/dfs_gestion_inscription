"""Zeus mode: the training-cohort interface (own layout and theme), switched on by the admin.
Admin, reception, residence, the coaches of a cohort and the registered students land here at login."""
from datetime import timedelta

from flask import Blueprint, abort, flash, make_response, redirect, render_template, request, url_for
from flask import session as cookie_session  # `session` is the session (séance) view below
from flask_login import current_user, login_required

from app.extensions import db
from app.models import Cohort, ZeusClass, ZeusClassMember, ZeusCourse, ZeusNotification, ZeusSession
from app.models.user import ROLE_ADMIN, ROLE_TRADE_COACH
from app.services import candidate_service, zeus_service
from app.services.candidate_service import BusinessError
from app.utils import allow_camera

bp = Blueprint("zeus", __name__, url_prefix="/zeus")


@bp.before_request
@login_required
def zeus_users_only():
    if not zeus_service.can_enter(current_user):
        abort(403)
    if not zeus_service.enabled() and current_user.role != ROLE_ADMIN:  # the admin prepares it before opening it
        flash("Le mode Zeus n'est pas activé.", "info")
        return redirect(url_for("dashboard.index"))
    if current_user.is_candidate and current_user.must_change_password:
        return redirect(url_for("portal.password"))
    return None


def _cohort(cohort_id: int) -> Cohort:
    cohort = db.get_or_404(Cohort, cohort_id)
    if not zeus_service.can_see(current_user, cohort):
        abort(404)
    return cohort


def _require_manage(cohort: Cohort) -> None:
    if not zeus_service.can_manage(current_user, cohort):
        abort(403)


def _back(default: str):
    return redirect(request.form.get("next") if (request.form.get("next") or "").startswith("/zeus") else default)


# ---- home

@bp.get("/")
def home():
    e = zeus_service.student_enrollment(current_user)
    week = zeus_service.week_start(None)
    splash = not cookie_session.get("zeus_splash")  # loading animation: once per session
    cookie_session["zeus_splash"] = True
    return render_template("zeus/home.html", e=e, cohorts=zeus_service.cohorts_for(current_user), splash=splash,
                           enabled=zeus_service.enabled(),
                           upcoming=zeus_service.sessions_for(current_user, week, week + timedelta(days=7)),
                           invitations=zeus_service.invitations_for(e) if e else [],
                           notifications=zeus_service.notifications_for(current_user, limit=3) if e else [],
                           classes=zeus_service.classes_for_student(e) if e else [])


@bp.post("/mode")
def mode():
    if current_user.role != ROLE_ADMIN:
        abort(403)
    on = request.form.get("enabled") == "1"
    zeus_service.set_enabled(on)
    flash("Mode Zeus activé : les utilisateurs Zeus y arrivent à la connexion." if on
          else "Mode Zeus désactivé : retour à l'interface classique pour tous.", "success")
    return redirect(url_for("zeus.home"))


# ---- cohorts: classes, courses, coaches

@bp.get("/cohortes/<int:cohort_id>")
def cohort(cohort_id: int):
    c = _cohort(cohort_id)
    return render_template("zeus/cohort.html", cohort=c, classes=zeus_service.classes_of(c),
                           courses=zeus_service.courses_of(c),
                           coaches=zeus_service.cohort_coaches(c),
                           can_manage=zeus_service.can_manage(current_user, c))


@bp.post("/cohortes/<int:cohort_id>/classes")
def class_create(cohort_id: int):
    c = _cohort(cohort_id)
    _require_manage(c)
    try:
        klass = zeus_service.create_class(c, request.form.get("name", ""), current_user)
        flash(f"Classe « {klass.name} » créée : invitez les étudiants.", "success")
        return redirect(url_for("zeus.klass", class_id=klass.id))
    except BusinessError as exc:
        flash(str(exc), "danger")
        return redirect(url_for("zeus.cohort", cohort_id=c.id))


@bp.post("/cohortes/<int:cohort_id>/cours")
def course_create(cohort_id: int):
    c = _cohort(cohort_id)
    _require_manage(c)
    try:
        course = zeus_service.save_course(c, request.form)
        flash(f"Cours « {course.name} » ajouté.", "success")
    except BusinessError as exc:
        db.session.rollback()
        flash(str(exc), "danger")
    return redirect(url_for("zeus.cohort", cohort_id=c.id) + "#cours")


@bp.route("/cours/<int:course_id>", methods=["GET", "POST"])
def course(course_id: int):
    course = db.get_or_404(ZeusCourse, course_id)
    c = _cohort(course.cohort_id)
    if request.method == "POST":
        _require_manage(c)
        try:
            zeus_service.save_course(c, request.form, course)
            flash("Cours enregistré.", "success")
        except BusinessError as exc:
            db.session.rollback()
            flash(str(exc), "danger")
        return redirect(url_for("zeus.course", course_id=course.id))
    return render_template("zeus/course.html", course=course, cohort=c, coaches=zeus_service.cohort_coaches(c),
                           can_manage=zeus_service.can_manage(current_user, c))


# ---- classes and invitations

@bp.get("/classes")
def classes():
    """Staff: the classes of their cohorts. Student: his classes and the invitations to answer."""
    e = zeus_service.student_enrollment(current_user)
    if e:
        return render_template("zeus/my_classes.html", e=e, classes=zeus_service.classes_for_student(e),
                               invitations=zeus_service.invitations_for(e))
    return render_template("zeus/classes.html", cohorts=[(c, zeus_service.classes_of(c),
                                                          zeus_service.can_manage(current_user, c))
                                                         for c in zeus_service.cohorts_for(current_user)])


@bp.route("/classes/<int:class_id>", methods=["GET", "POST"])
def klass(class_id: int):
    k = db.get_or_404(ZeusClass, class_id)
    c = _cohort(k.cohort_id)
    if zeus_service.student_enrollment(current_user):
        abort(404)  # students see their classes on /zeus/classes
    if request.method == "POST":
        _require_manage(c)
        if request.form.get("action") == "remove":
            member = next((m for m in k.members if m.id == request.form.get("member_id", type=int)), None)
            if member:
                zeus_service.remove_member(member)
                flash("Étudiant retiré de la classe.", "success")
        else:
            n = zeus_service.invite(k, request.form.getlist("enrollment_ids", type=int), current_user)
            flash(f"{n} étudiant(s) invité(s) : ils rejoignent la classe en acceptant depuis leur espace Zeus.",
                  "success" if n else "warning")
        return redirect(url_for("zeus.klass", class_id=k.id))
    return render_template("zeus/class.html", k=k, cohort=c, invitable=zeus_service.invitable(k),
                           can_manage=zeus_service.can_manage(current_user, c))


@bp.post("/invitations/<int:member_id>")
def answer(member_id: int):
    member = db.get_or_404(ZeusClassMember, member_id)
    e = zeus_service.student_enrollment(current_user)
    if e is None or member.enrollment_id != e.id or member.joined_at:
        abort(404)
    accept = request.form.get("accept") == "1"
    name = member.klass.name
    zeus_service.answer(member, accept)
    flash(f"Vous avez rejoint la classe « {name} »." if accept else "Invitation refusée.",
          "success" if accept else "info")
    return _back(url_for("zeus.classes"))


# ---- notifications

@bp.route("/notifications", methods=["GET", "POST"])
def notifications():
    if request.method == "POST":
        cohort_id, _, target = request.form.get("target", "").partition("/")  # "3/cohort", "3/class:5"...
        c = _cohort(int(cohort_id) if cohort_id.isdigit() else 0)
        if not zeus_service.can_notify(current_user, c):
            abort(403)
        try:
            n = zeus_service.send_notification(c, target, request.form, current_user)
            flash(f"Notification envoyée : {n.target_label}.", "success")
        except BusinessError as exc:
            db.session.rollback()
            flash(str(exc), "danger")
        return redirect(url_for("zeus.notifications"))
    senders = [c for c in zeus_service.cohorts_for(current_user) if zeus_service.can_notify(current_user, c)]
    return render_template("zeus/notifications.html", notifications=zeus_service.notifications_for(current_user),
                           targets=[(c, zeus_service.classes_of(c)) for c in senders],
                           student=zeus_service.student_enrollment(current_user) is not None)


@bp.post("/notifications/<int:notification_id>/supprimer")
def notification_delete(notification_id: int):
    n = db.get_or_404(ZeusNotification, notification_id)
    _cohort(n.cohort_id)
    if current_user.role != ROLE_ADMIN and n.created_by != current_user.id:
        abort(403)
    zeus_service.delete_notification(n)
    flash("Notification supprimée.", "success")
    return redirect(url_for("zeus.notifications"))


# ---- calendar (scheduled sessions)

@bp.route("/calendrier", methods=["GET", "POST"])
def calendar():
    if request.method == "POST":
        c = _cohort(request.form.get("cohort_id", 0, type=int))
        _require_manage(c)
        try:
            s = zeus_service.create_session(c, request.form, current_user)
            flash(f"Séance programmée : {s.course.name}.", "success")
            return redirect(url_for("zeus.calendar", week=request.form.get("starts_at", "")[:10], cohort=c.id))
        except BusinessError as exc:
            db.session.rollback()
            flash(str(exc), "danger")
        return redirect(url_for("zeus.calendar", cohort=c.id))
    cohort_id = request.args.get("cohort", type=int)
    selected = _cohort(cohort_id) if cohort_id else None
    start = zeus_service.week_start(request.args.get("week"))
    days = [start + timedelta(days=i) for i in range(7)]
    sessions = zeus_service.sessions_for(current_user, start, start + timedelta(days=7), selected)
    manageable = [c for c in zeus_service.cohorts_for(current_user) if zeus_service.can_manage(current_user, c)]
    return render_template("zeus/calendar.html", days=days, sessions=sessions, selected=selected,
                           today=candidate_service.today().isoformat(),
                           cohorts=zeus_service.cohorts_for(current_user), manageable=manageable,
                           prev=(start - timedelta(days=7)).date().isoformat(),
                           next=(start + timedelta(days=7)).date().isoformat(),
                           forms={c.id: (zeus_service.classes_of(c), zeus_service.cohort_coaches(c),
                                         zeus_service.courses_of(c)) for c in manageable})


# ---- sessions and roll calls

def _session(session_id: int) -> ZeusSession:
    s = db.get_or_404(ZeusSession, session_id)
    _cohort(s.course.cohort_id)
    e = zeus_service.student_enrollment(current_user)
    if e and s.class_id and s.class_id not in {k.id for k in zeus_service.classes_for_student(e)}:
        abort(404)
    return s


@bp.route("/seances/<int:session_id>", methods=["GET", "POST"])
def session(session_id: int):
    s = _session(session_id)
    can_call = zeus_service.can_call(current_user, s)
    if request.method == "POST":
        if not can_call:
            abort(403)
        if request.form.get("action") == "delete":
            _require_manage(s.course.cohort)
            zeus_service.delete_session(s)
            flash("Séance supprimée.", "success")
            return redirect(url_for("zeus.calendar", cohort=s.course.cohort_id))
        n = zeus_service.save_roll_call(s, set(request.form.getlist("present", type=int)), current_user)
        flash(f"Appel enregistré : {n} présent(s) sur {len(s.expected)}.", "success")
        return redirect(url_for("zeus.session", session_id=s.id))
    present = {m.enrollment_id for m in s.marks}
    return allow_camera(make_response(render_template(
        "zeus/session.html", s=s, present=present, can_call=can_call,
        can_manage=zeus_service.can_manage(current_user, s.course.cohort),
        me=zeus_service.student_enrollment(current_user))))


@bp.post("/seances/<int:session_id>/scan")
def session_scan(session_id: int):
    s = _session(session_id)
    if not zeus_service.can_call(current_user, s):
        abort(403)
    try:
        e, new = zeus_service.scan(s, request.form.get("token", ""), current_user)
        flash(f"{e.candidate.full_name} ({e.matricule}) : " + ("présent." if new else "déjà marqué présent."),
              "success")
    except BusinessError as exc:
        flash(str(exc), "danger")
    return redirect(url_for("zeus.session", session_id=s.id))


@bp.get("/appels")
def roll_calls():
    """Staff: the roll calls of the last 30 days. Student: his attendance."""
    e = zeus_service.student_enrollment(current_user)
    if e:
        return render_template("zeus/my_attendance.html", e=e, rows=zeus_service.student_attendance(e, current_user))
    end = zeus_service.week_start(None) + timedelta(days=7)
    sessions = zeus_service.sessions_for(current_user, end - timedelta(days=37), end)
    if current_user.role == ROLE_TRADE_COACH:
        sessions = [s for s in sessions if s.coach_id == current_user.id]  # coach métier: his sessions only
    return render_template("zeus/roll_calls.html", sessions=list(reversed(sessions)))
