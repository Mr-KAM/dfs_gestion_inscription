"""Candidate space: login, sign-up, own profile and password. Candidates only ever see their own data."""
from flask import Blueprint, abort, flash, make_response, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user

from app.auth import _safe_next, login_attempt
from app.extensions import db
from app.services import account_service, attendance_service, audit_service
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
    return render_template("portal/home.html", c=candidate, pending=pending)


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
