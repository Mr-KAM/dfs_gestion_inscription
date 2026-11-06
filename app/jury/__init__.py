"""Jury members' space: their juries, the shared timer, the defense sheets they rate, their digital signature."""
import io

from flask import Blueprint, abort, flash, redirect, render_template, request, send_file, url_for
from flask_login import current_user, login_required
from sqlalchemy import select

from app.extensions import db
from app.models import Defense, Jury, JuryCandidate, JuryMember, User
from app.models.defense import (DECISIONS, DEFENSE_CRITERIA, DEFENSE_SCALE, DEFENSE_SECTIONS, JURY_ROLES, NEGATIVES,
                                POSITIVES, SECTION_WEIGHTS, SUPPORTS, grid_points)
from app.models.user import ROLE_ADMIN
from app.services import audit_service, defense_service
from app.services.candidate_service import BusinessError
from app.utils import utcnow

bp = Blueprint("jury", __name__, url_prefix="/jury")


@bp.before_request
@login_required
def staff_only():
    """Team accounts only; each route then checks jury membership (or admin)."""
    if current_user.is_candidate:
        abort(403)


def _jury(jury_id: int) -> Jury:
    jury = db.get_or_404(Jury, jury_id)
    if current_user.role != ROLE_ADMIN and jury.member_of(current_user) is None:
        abort(403)
    return jury


def _row(row_id: int) -> JuryCandidate:
    row = db.get_or_404(JuryCandidate, row_id)
    if not defense_service.can_view(row, current_user):
        abort(403)
    return row


def _signature_owner(user_id: int) -> User:
    """Only the owner and the admin ever see or change a signature."""
    if current_user.id != user_id and current_user.role != ROLE_ADMIN:
        abort(403)
    return db.get_or_404(User, user_id)


@bp.get("/")
def index():
    memberships = db.session.scalars(select(JuryMember).join(Defense, Defense.id == JuryMember.defense_id)
                                     .where(JuryMember.user_id == current_user.id)
                                     .order_by(Defense.starts_at.desc(), JuryMember.id.desc())).all()
    rated = {m.jury_id: sum(any(s.user_id == current_user.id for s in row.scores) for row in m.jury.candidates)
             for m in memberships}  # students I have already rated, per jury
    return render_template("jury/index.html", memberships=memberships, rated=rated)


@bp.route("/signature/<int:user_id>", methods=["GET", "POST"])
def signature(user_id: int):
    owner = _signature_owner(user_id)
    if request.method == "GET":
        if not owner.signature_type:
            abort(404)
        response = send_file(io.BytesIO(owner.signature), mimetype=owner.signature_type, max_age=0)
        response.headers["Cache-Control"] = "private, no-store"
        return response
    try:
        defense_service.save_signature(owner, request.files.get("signature"), remove="remove" in request.form)
        flash("Signature supprimée." if "remove" in request.form else "Signature enregistrée.", "success")
    except BusinessError as exc:
        db.session.rollback()
        flash(str(exc), "danger")
    defense_id = request.form.get("defense_id", type=int)
    if defense_id and current_user.role == ROLE_ADMIN:
        return redirect(url_for("defense.detail", defense_id=defense_id))
    return redirect(url_for("jury.index"))


@bp.get("/<int:jury_id>")
def jury_page(jury_id: int):
    jury = _jury(jury_id)
    return render_template("jury/jury.html", jury=jury, me=jury.member_of(current_user),
                           results={row.id: defense_service.results(row) for row in jury.candidates})


@bp.route("/<int:jury_id>/timer", methods=["GET", "POST"])
def timer(jury_id: int):
    """GET: the polled timer fragment. POST: start (minutes) / pause / resume / reset."""
    jury = _jury(jury_id)
    if request.method == "GET":
        return render_template("jury/_timer.html", jury=jury)
    try:
        defense_service.set_timer(jury, current_user, request.form.get("action", ""),
                                  request.form.get("minutes", type=int))
    except BusinessError as exc:
        flash(str(exc), "danger")
    row_id = request.form.get("row_id", type=int)
    return redirect(url_for("jury.sheet", row_id=row_id) if row_id else url_for("jury.jury_page", jury_id=jury.id))


SHEET_LABELS = {"criteria": DEFENSE_CRITERIA, "sections": DEFENSE_SECTIONS, "section_weights": SECTION_WEIGHTS,
                "scale": DEFENSE_SCALE, "supports": SUPPORTS, "positives": POSITIVES, "negatives": NEGATIVES,
                "decisions": DECISIONS, "roles": JURY_ROLES, "points": grid_points}


def _sheet_context(row: JuryCandidate) -> dict:
    present, total = defense_service.attendance(row.candidate)
    return {"row": row, "jury": row.jury, "c": row.candidate, "s": row.sheet or {}, "res": defense_service.results(row),
            "present": present, "total_hours": total, "position": row.jury.candidates.index(row), **SHEET_LABELS}


@bp.get("/sheets/<int:row_id>")
def sheet(row_id: int):
    row = _row(row_id)
    me = row.jury.member_of(current_user)
    mine = next((s for s in row.scores if me and s.user_id == me.user_id), None)
    return render_template("jury/sheet.html", **_sheet_context(row), me=me, mine=mine,
                           editable=row.jury.validated_at is None)


@bp.post("/sheets/<int:row_id>/score")
def score(row_id: int):
    row = _row(row_id)
    try:
        defense_service.save_score(row, current_user, request.form)
        flash("Votre notation est enregistrée.", "success")
    except BusinessError as exc:
        db.session.rollback()
        flash(str(exc), "danger")
    return redirect(url_for("jury.sheet", row_id=row.id) + "#grille")


@bp.post("/sheets/<int:row_id>/info")
def info(row_id: int):
    row = _row(row_id)
    try:
        defense_service.save_sheet(row, current_user, request.form)
        flash("Fiche enregistrée.", "success")
    except BusinessError as exc:
        db.session.rollback()
        flash(str(exc), "danger")
    return redirect(url_for("jury.sheet", row_id=row.id) + ("#decision" if request.form.get("part") == "appraisal"
                                                            else ""))


@bp.get("/sheets/<int:row_id>/print")
def print_sheet(row_id: int):
    """Final sheet with every result and the members' signatures: admin only, once the deliberation is validated."""
    row = db.get_or_404(JuryCandidate, row_id)
    if current_user.role != ROLE_ADMIN:
        abort(403)
    if row.jury.validated_at is None:
        flash("Validez d'abord la délibération du jury.", "warning")
        return redirect(url_for("defense.detail", defense_id=row.defense_id) + f"#jury-{row.jury_id}")
    return render_template("jury/print.html", sheets=[_sheet_context(row)], **SHEET_LABELS, printed_at=utcnow(),
                           doc_title=f"Procès-verbal de soutenance · {row.candidate.full_name}")


@bp.get("/soutenances/<int:defense_id>/proces-verbaux")
def print_all(defense_id: int):
    """Every student's minutes in one document, one sheet each, by name: admin only, once all is validated."""
    if current_user.role != ROLE_ADMIN:
        abort(403)
    defense = db.get_or_404(Defense, defense_id)
    problem = defense_service.minutes_problem(defense)
    if problem:
        flash(problem, "warning")
        return redirect(url_for("defense.global_jury", defense_id=defense.id))
    rows = sorted((row for jury in defense.juries for row in jury.candidates), key=lambda r: r.candidate.full_name)
    audit_service.log("export", defense, {"kind": "defense_all_minutes", "rows": len(rows)})
    db.session.commit()
    return render_template("jury/print.html", sheets=[_sheet_context(row) for row in rows], **SHEET_LABELS,
                           printed_at=utcnow(), doc_title=f"Procès-verbaux individuels · {defense.name}")
