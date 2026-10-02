import re

from flask import Blueprint, flash, redirect, render_template, request, url_for
from sqlalchemy import func, select

from app.extensions import db
from app.models import Candidate, KoboSubmission
from app.models.kobo import MATCH_AMBIGUOUS, MATCH_UNMATCHED
from app.models.user import ROLE_ADMIN
from app.services import audit_service, kobo_service, settings_service
from app.services.settings_service import KOBO_FIELDS
from app.utils import roles_required

bp = Blueprint("kobo", __name__, url_prefix="/admin/kobo")


@bp.get("/")
@roles_required(ROLE_ADMIN)
def index():
    to_check = db.session.scalars(
        select(KoboSubmission).where(KoboSubmission.match_status.in_((MATCH_AMBIGUOUS, MATCH_UNMATCHED)))
        .order_by(KoboSubmission.match_status, KoboSubmission.submitted_at.desc())
    ).all()
    counts = dict(db.session.execute(
        select(KoboSubmission.match_status, func.count()).group_by(KoboSubmission.match_status)
    ).all())
    fields = {key: settings_service.get(key) for key in KOBO_FIELDS}
    candidates = db.session.execute(select(Candidate.id, Candidate.full_name, Candidate.cni).order_by(Candidate.full_name)).all()
    return render_template(
        "kobo/index.html", to_check=to_check, counts=counts, fields=fields, field_labels=KOBO_FIELDS,
        base_url=settings_service.get("kobo_base_url"), asset_uid=settings_service.get("kobo_asset_uid"),
        token_set=bool(settings_service.get("kobo_token")), last_sync=settings_service.get("kobo_last_sync"),
        candidates=candidates, get_field=kobo_service.get_field,
    )


@bp.post("/config")
@roles_required(ROLE_ADMIN)
def save_config():
    values = {"kobo_base_url": request.form.get("kobo_base_url", ""), "kobo_asset_uid": request.form.get("kobo_asset_uid", "")}
    if not values["kobo_base_url"].startswith(("https://", "http://")):
        flash("L'URL Kobo doit commencer par https://", "danger")
        return redirect(url_for("kobo.index"))
    values.update({key: request.form.get(key, "") for key in KOBO_FIELDS})
    if request.form.get("kobo_token"):  # blank field keeps the stored token
        values["kobo_token"] = request.form["kobo_token"]
    settings_service.set_many(values)
    audit_service.log("kobo_config", details={k: v for k, v in values.items() if k != "kobo_token"} | {
        "token_changed": "kobo_token" in values})
    db.session.commit()
    flash("Configuration Kobo enregistrée.", "success")
    return redirect(url_for("kobo.index"))


@bp.post("/test")
@roles_required(ROLE_ADMIN)
def test_connection():
    try:
        name = kobo_service.client_from_settings().test_connection()
        flash(f"Connexion réussie — formulaire « {name} ».", "success")
    except kobo_service.KoboError as exc:
        flash(f"Erreur de connexion : {exc}", "danger")
    return redirect(url_for("kobo.index"))


@bp.post("/sync")
@roles_required(ROLE_ADMIN)
def sync_now():
    try:
        r = kobo_service.sync()
        flash(f"Synchronisation terminée : {r['total']} soumissions, {r['matched']} associées, "
              f"{r['ambiguous']} à vérifier, {r['unmatched']} sans correspondance.", "success")
    except kobo_service.KoboError as exc:
        db.session.rollback()
        flash(f"Erreur de synchronisation : {exc}", "danger")
    return redirect(url_for("kobo.index"))


@bp.post("/submissions/<int:submission_id>/resolve")
@roles_required(ROLE_ADMIN)
def resolve(submission_id: int):
    submission = db.get_or_404(KoboSubmission, submission_id)
    match = re.match(r"\s*(\d+)", request.form.get("candidate", ""))
    candidate = db.session.get(Candidate, int(match.group(1))) if match else None
    if candidate is None:
        flash("Candidat introuvable : choisissez-le dans la liste.", "danger")
    else:
        kobo_service.resolve(submission, candidate)
        flash(f"Soumission associée à {candidate.full_name}.", "success")
    return redirect(url_for("kobo.index"))
