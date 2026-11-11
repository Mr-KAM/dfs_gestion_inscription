"""Training cohorts: the admin creates the academic year and picks the eligible students (admitted at a SAS
defense); reception and residence register them (arrival, signature, bedroom, equipment)."""
import io

from flask import Blueprint, abort, flash, redirect, render_template, request, send_file, url_for
from flask_login import current_user
from sqlalchemy import select

from app.extensions import db
from app.models import Cohort, Enrollment, Sas
from app.models.user import ROLE_ADMIN, ROLE_RECEPTION, ROLE_RESIDENCE
from app.services import cohort_service
from app.services.candidate_service import BusinessError
from app.utils import roles_required

bp = Blueprint("cohort", __name__, url_prefix="/cohortes")


@bp.before_request
@roles_required(ROLE_ADMIN, ROLE_RECEPTION, ROLE_RESIDENCE)
def require_desk():
    """Every /cohortes route: admin, reception or residence; creation and eligibility: admin only (below)."""


def _require_admin() -> None:
    if current_user.role != ROLE_ADMIN:
        abort(403)


@bp.route("/", methods=["GET", "POST"])
def index():
    if request.method == "POST":
        _require_admin()
        try:
            cohort = cohort_service.create(request.form)
            flash(f"Cohorte « {cohort.name} » créée : ajoutez les étudiants admis.", "success")
            return redirect(url_for("cohort.detail", cohort_id=cohort.id))
        except BusinessError as exc:
            flash(str(exc), "danger")
    cohorts = db.session.scalars(select(Cohort).order_by(Cohort.created_at.desc())).all()
    return render_template("cohort/index.html", cohorts=cohorts)


@bp.get("/<int:cohort_id>")
def detail(cohort_id: int):
    cohort = db.get_or_404(Cohort, cohort_id)
    q = request.args.get("q", "").strip().lower()
    rows = [e for e in cohort.enrollments
            if not q or q in " ".join((e.candidate.full_name, e.candidate.matricule, e.matricule or "")).lower()]
    sas_id = request.args.get("sas", type=int)
    return render_template("cohort/detail.html", cohort=cohort, rows=rows, q=q, sas_id=sas_id,
                           sas_list=db.session.scalars(select(Sas).order_by(Sas.name)).all(),
                           admitted=cohort_service.admitted(sas_id) if sas_id and current_user.role == ROLE_ADMIN
                           else [])


@bp.post("/<int:cohort_id>/eligibles")
def add_eligible(cohort_id: int):
    _require_admin()
    cohort = db.get_or_404(Cohort, cohort_id)
    added = cohort_service.add_eligible(cohort, request.form.get("sas_id", 0, type=int),
                                        request.form.getlist("candidate_ids", type=int))
    flash(f"{added} étudiant(s) éligible(s) ajouté(s) à la cohorte.", "success" if added else "warning")
    return redirect(url_for("cohort.detail", cohort_id=cohort.id))


@bp.route("/inscription/<int:enrollment_id>", methods=["GET", "POST"])
def enrollment(enrollment_id: int):
    """Registration page of one student: the desk validates it and gets him his DFS matricule."""
    e = db.get_or_404(Enrollment, enrollment_id)
    if request.method == "POST":
        if request.form.get("action") == "remove":
            _require_admin()
            try:
                cohort_service.remove(e)
                flash("Étudiant retiré de la cohorte.", "success")
                return redirect(url_for("cohort.detail", cohort_id=e.cohort_id))
            except BusinessError as exc:
                flash(str(exc), "danger")
                return redirect(url_for("cohort.enrollment", enrollment_id=e.id))
        validate = request.form.get("action") == "validate" and not e.validated_at
        try:
            cohort_service.save(e, request.form, validate, current_user)
            flash(f"Inscription validée : matricule {e.matricule}." if validate else "Inscription enregistrée.",
                  "success")
        except BusinessError as exc:
            db.session.rollback()
            flash(str(exc), "danger")
        return redirect(url_for("cohort.enrollment", enrollment_id=e.id))
    return render_template("cohort/enrollment.html", e=e, c=e.candidate)


@bp.get("/inscription/<int:enrollment_id>/signature")
def signature(enrollment_id: int):
    e = db.get_or_404(Enrollment, enrollment_id)
    if not e.signature_type:
        abort(404)
    response = send_file(io.BytesIO(e.signature), mimetype=e.signature_type, max_age=0)
    response.headers["Cache-Control"] = "private, no-cache"
    return response
