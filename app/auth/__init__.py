from urllib.parse import urlsplit

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user, logout_user
from sqlalchemy import func, select

from app.extensions import db
from app.models import User
from app.services import audit_service
from app.utils import utcnow

bp = Blueprint("auth", __name__)
LOGIN_FAILED = "Email ou mot de passe incorrect, ou compte désactivé."


def _safe_next(target: str | None) -> str:
    """Only allow relative redirects (open-redirect protection)."""
    if target and target.startswith("/") and not target.startswith("//") and not urlsplit(target).netloc:
        return target
    return url_for("dashboard.index")


def authenticate(email: str, password: str) -> User | None:
    """Log the user in if the credentials are valid; audit both outcomes."""
    email = email.strip().lower()
    user = db.session.scalar(select(User).where(func.lower(User.email) == email))
    if user and user.check_password(password) and user.active:
        login_user(user, remember=False)
        user.last_login_at = utcnow()
        audit_service.log("login", user, user=user)
        db.session.commit()
        return user
    audit_service.log("login_failed", details={"email": email[:255]})
    db.session.commit()
    return None


@bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard.index"))
    if request.method == "POST":
        if authenticate(request.form.get("email", ""), request.form.get("password", "")):
            return redirect(_safe_next(request.args.get("next")))
        # Same message whatever failed: no account enumeration.
        flash(LOGIN_FAILED, "danger")
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
