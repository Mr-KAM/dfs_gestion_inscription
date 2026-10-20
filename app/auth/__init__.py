from urllib.parse import urlsplit

from flask import Blueprint, abort, flash, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required, login_user, logout_user
from sqlalchemy import func, select
from werkzeug.security import check_password_hash, generate_password_hash

from app.extensions import db
from app.models import User
from app.services import account_service, audit_service
from app.services.candidate_service import BusinessError
from app.utils import utcnow

bp = Blueprint("auth", __name__)
LOGIN_FAILED = "Email ou mot de passe incorrect, ou compte désactivé."
LOGIN_BLOCKED = "Trop de tentatives de connexion. Réessayez dans 15 minutes."
# Sliding window over failed logins. Per email: stops guessing one account (e.g. the shared initial
# candidate password). Per IP: generous, since a whole test room may share one public IP.
WINDOW_MINUTES, MAX_FAILS_PER_EMAIL, MAX_FAILS_PER_IP = 15, 5, 30
# Checked when the email is unknown, so both cases take the same time (no email enumeration by timing).
_DUMMY_HASH = generate_password_hash("timing-equaliser")


class LoginBlocked(Exception):
    pass


def _safe_next(target: str | None) -> str:
    """Only allow relative redirects (open-redirect protection)."""
    if target and target.startswith("/") and not target.startswith("//") and not urlsplit(target).netloc:
        return target
    return url_for("dashboard.index")


def authenticate(email: str, password: str) -> User | None:
    """Log the user in if the credentials are valid; audit both outcomes. Raises LoginBlocked."""
    email = email.strip().lower()[:255]
    if (audit_service.recent_count("login_failed", WINDOW_MINUTES, email=email) >= MAX_FAILS_PER_EMAIL
            or audit_service.recent_count("login_failed", WINDOW_MINUTES, ip=request.remote_addr) >= MAX_FAILS_PER_IP):
        audit_service.log("login_blocked", details={"email": email})
        db.session.commit()
        raise LoginBlocked(LOGIN_BLOCKED)
    user = db.session.scalar(select(User).where(func.lower(User.email) == email))
    if user is None:
        check_password_hash(_DUMMY_HASH, password)  # same cost as a real check
        valid = False
    else:
        valid = user.check_password(password)
    if valid and user.active:
        login_user(user, remember=False)
        session.permanent = True  # expires after PERMANENT_SESSION_LIFETIME (shared PCs in test rooms)
        user.last_login_at = utcnow()
        audit_service.log("login", user, user=user)
        db.session.commit()
        return user
    audit_service.log("login_failed", details={"email": email})
    db.session.commit()
    return None


def login_attempt(email: str, password: str) -> User | None:
    """authenticate() + the user-facing message on failure (shared by staff and candidate login pages)."""
    try:
        user = authenticate(email, password)
    except LoginBlocked as exc:
        flash(str(exc), "danger")
        return None
    if user is None:
        flash(LOGIN_FAILED, "danger")  # same message whatever failed: no account enumeration
    return user


@bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard.index"))
    if request.method == "POST" and login_attempt(request.form.get("email", ""), request.form.get("password", "")):
        return redirect(_safe_next(request.args.get("next")))
    return render_template("auth/login.html")


@bp.post("/logout")
@login_required
def logout():
    was_candidate = current_user.is_candidate
    audit_service.log("logout", current_user._get_current_object())
    db.session.commit()
    logout_user()
    flash("Vous êtes déconnecté.", "info")
    return redirect(url_for("portal.login" if was_candidate else "auth.login"))


@bp.route("/profil", methods=["GET", "POST"])
@login_required
def profile():
    """Staff profile: name, phone, photo, password. Candidates have their own page."""
    if current_user.is_candidate:
        return redirect(url_for("portal.profile"))
    user = current_user._get_current_object()
    if request.method == "POST":
        try:
            if request.form.get("action") == "password":
                form = request.form
                account_service.change_password(user, form.get("current_password", ""), form.get("password", ""),
                                                form.get("password_confirm", ""))
                login_user(user)  # new password = new session id; other sessions are logged out
                flash("Mot de passe modifié.", "success")
            else:
                account_service.update_staff_profile(user, request.form, request.files.get("photo"))
                flash("Profil mis à jour.", "success")
            return redirect(url_for("auth.profile"))
        except BusinessError as exc:
            db.session.rollback()
            flash(str(exc), "danger")
    return render_template("auth/profile.html", user=user)


@bp.get("/profil/photo")
@login_required
def photo():
    if current_user.is_candidate:
        abort(404)
    return account_service.photo_response(current_user._get_current_object())  # own photo only
