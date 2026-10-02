from flask import Blueprint, flash, redirect, request, url_for
from flask_login import current_user

from app.candidates import render_list
from app.extensions import db
from app.models import Candidate
from app.models.user import ROLE_ADMIN, ROLE_MOTIVATION
from app.services import candidate_service
from app.utils import roles_required

bp = Blueprint("motivation", __name__, url_prefix="/motivation")


@bp.get("/")
@roles_required(ROLE_ADMIN, ROLE_MOTIVATION)
def index():
    """Today's candidates still waiting for the motivation interview (filters editable)."""
    defaults = {"date": candidate_service.today().isoformat(), "motivation": "pending", "sort": "order"}
    return render_list("motivation", "Tests de motivation", defaults)


@bp.post("/<int:candidate_id>/validate")
@roles_required(ROLE_ADMIN, ROLE_MOTIVATION)
def validate(candidate_id: int):
    candidate = db.get_or_404(Candidate, candidate_id)
    try:
        candidate_service.validate_motivation(
            candidate, request.form.get("piece_number", ""), request.form.get("exam_code", ""),
            current_user._get_current_object(),
        )
        flash(f"Motivation terminée — code examen {candidate.exam_code}.", "success")
    except candidate_service.BusinessError as exc:
        flash(str(exc), "danger")
    return redirect(url_for("candidates.detail", candidate_id=candidate.id))
