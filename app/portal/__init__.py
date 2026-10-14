"""Candidate space: login, sign-up, own profile and password. Candidates only ever see their own data."""
from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user

from app.auth import LOGIN_FAILED, authenticate
from app.services import account_service
from app.services.candidate_service import BusinessError

bp = Blueprint("portal", __name__, url_prefix="/candidat")
PUBLIC_ENDPOINTS = {"portal.login", "portal.signup"}


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
    if request.method == "POST":
        if authenticate(request.form.get("email", ""), request.form.get("password", "")):
            return redirect(url_for("dashboard.index"))
        flash(LOGIN_FAILED, "danger")
    return render_template("portal/login.html")


@bp.route("/inscription", methods=["GET", "POST"])
def signup():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard.index"))
    if request.method == "POST":
        try:
            login_user(account_service.register(request.form))
            flash("Votre compte est créé. Bienvenue !", "success")
            return redirect(url_for("portal.home"))
        except BusinessError as exc:
            flash(str(exc), "danger")
    return render_template("portal/signup.html")


@bp.get("/")
@login_required
def home():
    return render_template("portal/home.html", c=current_user.candidate)


@bp.route("/mot-de-passe", methods=["GET", "POST"])
@login_required
def password():
    if request.method == "POST":
        try:
            account_service.change_password(current_user, request.form.get("current_password", ""),
                                            request.form.get("password", ""), request.form.get("password_confirm", ""))
            flash("Mot de passe modifié.", "success")
            return redirect(url_for("portal.home"))
        except BusinessError as exc:
            flash(str(exc), "danger")
    return render_template("portal/password.html")
