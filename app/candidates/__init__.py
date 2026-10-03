from datetime import date

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy import select

from app.extensions import db
from app.models import Candidate, Room
from app.models.candidate import FILE_STATUSES
from app.models.user import ALL_ROLES, MOTIVATION_ROLES, ROLE_ADMIN, ROLE_MOTIVATION, ROLE_TECHNICAL, TECHNICAL_ROLES  # noqa: E501
from app.services import audit_service, candidate_service, settings_service
from app.utils import norm_email, norm_phone, roles_required

bp = Blueprint("candidates", __name__, url_prefix="/candidates")


def render_list(view: str, title: str, defaults: dict | None = None):
    """Shared candidate table for admin / motivation / technical views."""
    args = request.args.to_dict()
    if defaults and not any(k in args for k in ("q", "group", "date", "gender", "kobo", "motivation", "technical", "page")):
        args.update(defaults)
    stmt = candidate_service.filtered_query(args, eligible_only=view == "technical")
    page = db.paginate(stmt, page=request.args.get("page", 1, type=int), per_page=current_app.config["PER_PAGE"])
    return render_template("candidates/list.html", page=page, view=view, title=title, args=args,
                           groups=candidate_service.distinct_groups())


def get_visible_candidate(candidate_id: int) -> Candidate:
    candidate = db.get_or_404(Candidate, candidate_id)
    if current_user.role == ROLE_TECHNICAL and not candidate.motivation_completed:
        abort(404)  # technical testers only see eligible candidates
    return candidate


@bp.get("/")
@roles_required(*ALL_ROLES)
def index():
    if current_user.role == ROLE_TECHNICAL:
        return render_list("technical", "Candidats éligibles")
    if current_user.role == ROLE_MOTIVATION:
        return render_list("motivation", "Candidats")
    return render_list("admin", "Candidats")


@bp.get("/<int:candidate_id>")
@roles_required(*ALL_ROLES)
def detail(candidate_id: int):
    candidate = get_visible_candidate(candidate_id)
    rooms = db.session.scalars(select(Room).where(Room.active.is_(True)).order_by(Room.name)).all()
    return render_template(
        "candidates/detail.html", c=candidate, rooms=rooms,
        default_duration=settings_service.get_int("technical_duration", current_app.config["DEFAULT_TECHNICAL_TEST_DURATION"]),
        # Only an admin may correct an already validated motivation.
        can_edit_motivation=current_user.role == ROLE_ADMIN or (
            current_user.role in MOTIVATION_ROLES and not candidate.motivation_completed),
        can_run_technical=current_user.role in TECHNICAL_ROLES,
    )


def _parse_date(value: str):
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        raise candidate_service.BusinessError(f"Date invalide : {value}")


def _apply_form(candidate: Candidate, form) -> None:
    full_name = " ".join(form.get("full_name", "").split())
    if not full_name:
        raise candidate_service.BusinessError("Le nom et prénoms est obligatoire.")
    order = form.get("passage_order", "").strip()
    if order and not order.isdigit():
        raise candidate_service.BusinessError("L'ordre de passage doit être un nombre.")
    status = form.get("physical_file_status") or None
    if status and status not in FILE_STATUSES:
        raise candidate_service.BusinessError("Statut de dossier invalide.")
    candidate.full_name = full_name[:200]
    candidate.candidate_number = form.get("candidate_number", "").strip()[:50] or None
    candidate.group_name = form.get("group_name", "").strip()[:50] or None
    candidate.scheduled_date = _parse_date(form.get("scheduled_date", ""))
    candidate.scheduled_time = form.get("scheduled_time", "").strip()[:30] or None
    candidate.passage_order = int(order) if order else None
    candidate.birth_date = _parse_date(form.get("birth_date", ""))
    candidate.gender = form.get("gender") if form.get("gender") in ("M", "F") else None
    candidate.email = norm_email(form.get("email"))[:255] or None
    candidate.phone = norm_phone(form.get("phone")) or None
    candidate.city = form.get("city", "").strip()[:120] or None
    candidate.cni = form.get("cni", "").strip().upper()[:50] or None
    candidate.physical_file_status = status


@bp.route("/new", methods=["GET", "POST"])
@roles_required(ROLE_ADMIN)
def create():
    candidate = Candidate()
    if request.method == "POST":
        try:
            _apply_form(candidate, request.form)
            db.session.add(candidate)
            db.session.flush()
            audit_service.log("candidate_create", candidate, {"full_name": candidate.full_name})
            db.session.commit()
            flash("Candidat créé.", "success")
            return redirect(url_for("candidates.detail", candidate_id=candidate.id))
        except candidate_service.BusinessError as exc:
            db.session.rollback()
            flash(str(exc), "danger")
    return render_template("candidates/form.html", c=candidate, file_statuses=FILE_STATUSES)


@bp.route("/<int:candidate_id>/edit", methods=["GET", "POST"])
@roles_required(ROLE_ADMIN)
def edit(candidate_id: int):
    candidate = db.get_or_404(Candidate, candidate_id)
    if request.method == "POST":
        try:
            _apply_form(candidate, request.form)
            audit_service.log("candidate_update", candidate, {"fields": sorted(k for k in request.form if k != "csrf_token")})
            db.session.commit()
            flash("Candidat mis à jour.", "success")
            return redirect(url_for("candidates.detail", candidate_id=candidate.id))
        except candidate_service.BusinessError as exc:
            db.session.rollback()
            flash(str(exc), "danger")
    return render_template("candidates/form.html", c=candidate, file_statuses=FILE_STATUSES)


@bp.post("/<int:candidate_id>/delete")
@roles_required(ROLE_ADMIN)
def delete(candidate_id: int):
    candidate = db.get_or_404(Candidate, candidate_id)
    if candidate.active_session:
        flash("Impossible de supprimer un candidat dont le test technique est en cours.", "danger")
        return redirect(url_for("candidates.detail", candidate_id=candidate.id))
    audit_service.log("candidate_delete", candidate, {"full_name": candidate.full_name, "exam_code": candidate.exam_code})
    db.session.delete(candidate)
    db.session.commit()
    flash("Candidat supprimé.", "success")
    return redirect(url_for("candidates.index"))
