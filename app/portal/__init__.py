"""Candidate space: login, sign-up, own profile and password. Candidates only ever see their own data."""
from flask import Blueprint, abort, flash, make_response, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user

from app.auth import _safe_next, login_attempt
from app.extensions import db
from app.models import ClassInvitation, Exercise
from app.services import account_service, attendance_service, audit_service, campus_service
from app.services.candidate_service import BusinessError
from app.utils import allow_camera

bp = Blueprint("portal", __name__, url_prefix="/candidat")
PUBLIC_ENDPOINTS = {"portal.login", "portal.signup"}
MAX_SIGNUPS_PER_IP_HOUR = 10  # attempts (accepted or refused): limits spam and email probing


@bp.before_request
def candidates_only():
    if request.endpoint in PUBLIC_ENDPOINTS or not current_user.is_authenticated:
        return None
    if not current_user.is_candidate:
        abort(403)
    # Default password (00000000): must be replaced before anything else.
    if current_user.must_change_password and request.endpoint != "portal.password":
        flash("Pour sécuriser votre compte, choisissez un nouveau mot de passe.", "warning")
        return redirect(url_for("portal.password"))
    return None


@bp.route("/connexion", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard.index"))
    if request.method == "POST" and login_attempt(request.form.get("email", ""), request.form.get("password", "")):
        return redirect(_safe_next(request.args.get("next")))  # e.g. back to a scanned check-in QR code
    return render_template("portal/login.html")


@bp.route("/inscription", methods=["GET", "POST"])
def signup():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard.index"))
    if request.method == "POST":
        ip = request.remote_addr
        attempts = sum(audit_service.recent_count(a, 60, ip=ip) for a in ("candidate_signup", "signup_rejected"))
        if attempts >= MAX_SIGNUPS_PER_IP_HOUR:
            flash("Trop d'inscriptions depuis ce réseau. Réessayez plus tard ou adressez-vous à l'équipe DFS.", "danger")
            return render_template("portal/signup.html"), 429
        try:
            login_user(account_service.register(request.form))
            flash("Votre compte est créé. Bienvenue !", "success")
            return redirect(url_for("portal.home"))
        except BusinessError as exc:
            audit_service.log("signup_rejected", details={"reason": str(exc)[:120]})
            db.session.commit()
            flash(str(exc), "danger")
    return render_template("portal/signup.html")


@bp.get("/")
@login_required
def home():
    candidate = current_user.candidate
    marks = attendance_service.marks_by_session(candidate)
    pending = [s for s in attendance_service.sessions_for(candidate) if s.is_open and s.id not in marks]
    return render_template("portal/home.html", c=candidate, pending=pending,
                           notifications=campus_service.notifications_for(candidate, limit=3),
                           invitations=campus_service.pending_invitations(candidate))


@bp.get("/notifications")
@login_required
def notifications():
    return render_template("portal/notifications.html",
                           notifications=campus_service.notifications_for(current_user.candidate),
                           invitations=campus_service.pending_invitations(current_user.candidate))


@bp.route("/exercices", methods=["GET", "POST"])
@login_required
def exercises():
    candidate = current_user.candidate
    if request.method == "POST":
        exercise = db.session.get(Exercise, request.form.get("exercise_id", 0, type=int))
        if exercise is None or exercise not in campus_service.exercises_for(candidate):
            abort(404)  # not published yet, or not for this student's class
        try:
            campus_service.submit_answer(exercise, candidate, request.form.get("answer", ""))
            flash("Réponse envoyée à votre coach.", "success")
        except BusinessError as exc:
            flash(str(exc), "danger")
        return redirect(url_for("portal.exercises") + f"#e{exercise.id}")
    items = campus_service.exercises_for(candidate)
    submissions = {s.exercise_id: s for e in items for s in e.submissions if s.candidate_id == candidate.id}
    return render_template("portal/exercises.html", exercises=items, submissions=submissions)


@bp.route("/profil", methods=["GET", "POST"])
@login_required
def profile():
    candidate = current_user.candidate
    if request.method == "POST":
        try:
            account_service.update_profile(candidate, request.form, request.files.get("photo"))
            flash("Profil mis à jour.", "success")
            return redirect(url_for("portal.home"))
        except BusinessError as exc:
            db.session.rollback()
            flash(str(exc), "danger")
    return render_template("portal/profile.html", c=candidate)


@bp.get("/photo")
@login_required
def photo():
    return account_service.photo_response(current_user.candidate)  # own photo only: no id in the url


@bp.route("/presence", methods=["GET", "POST"])
@login_required
def attendance():
    """Check-in: scan the QR code shown by the admin (in-page scanner, or the phone camera opening ?code=...)."""
    candidate = current_user.candidate
    if request.method == "POST":
        try:
            session, new = attendance_service.self_mark(candidate, request.form.get("token", ""), current_user)
            flash(f"Présence enregistrée : {session.label}." if new else "Votre présence était déjà enregistrée.",
                  "success")
        except BusinessError as exc:
            flash(str(exc), "danger")
        return redirect(url_for("portal.attendance"))
    sessions = attendance_service.sessions_for(candidate)
    return allow_camera(make_response(render_template(
        "portal/attendance.html", c=candidate, sessions=sessions, code=request.args.get("code", "")[:200],
        marks=attendance_service.marks_by_session(candidate))))


@bp.route("/classe", methods=["GET", "POST"])
@login_required
def join_class():
    """Join a class: QR code scanned (phone camera opens ?code=..., or the in-page scanner), or address / code typed."""
    candidate = current_user.candidate
    if request.method == "POST":
        try:
            klass = campus_service.join_with_code(candidate, request.form.get("token", ""))
            flash(f"Vous avez rejoint la classe « {klass.name} ».", "success")
        except BusinessError as exc:
            flash(str(exc), "danger")
        return redirect(url_for("portal.join_class"))
    target = None
    if request.args.get("code"):
        try:
            target = campus_service.class_from_code(request.args["code"])
        except BusinessError as exc:
            flash(str(exc), "danger")
    return allow_camera(make_response(render_template(
        "portal/class.html", c=candidate, target=target, code=request.args.get("code", "")[:50],
        invitations=campus_service.pending_invitations(candidate))))


@bp.post("/invitations/<int:invitation_id>")
@login_required
def answer_invitation(invitation_id: int):
    invitation = db.get_or_404(ClassInvitation, invitation_id)
    accept = request.form.get("accept") == "1"
    try:
        campus_service.answer_invitation(invitation, current_user.candidate, accept)
        flash(f"Vous avez rejoint la classe « {invitation.klass.name} »." if accept else "Invitation refusée.",
              "success" if accept else "info")
    except BusinessError as exc:
        flash(str(exc), "danger")
    return redirect(_safe_next(request.form["next"]) if request.form.get("next") else url_for("portal.join_class"))


@bp.route("/mot-de-passe", methods=["GET", "POST"])
@login_required
def password():
    if request.method == "POST":
        try:
            account_service.change_password(current_user, request.form.get("current_password", ""),
                                            request.form.get("password", ""), request.form.get("password_confirm", ""))
            login_user(current_user._get_current_object())  # new password = new session id; others are logged out
            flash("Mot de passe modifié.", "success")
            return redirect(url_for("portal.home"))
        except BusinessError as exc:
            flash(str(exc), "danger")
    return render_template("portal/password.html")
