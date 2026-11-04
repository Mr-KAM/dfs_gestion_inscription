"""SAS defenses (admin): schedule a defense, its juries (members, duration per student) and who defends where."""
from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy import select

from app.extensions import db
from app.models import Defense, Jury, JuryCandidate, JuryMember, Sas
from app.models.defense import JURY_ROLES
from app.models.user import ROLE_ADMIN
from app.services import audit_service, defense_service
from app.services.candidate_service import BusinessError
from app.utils import roles_required

bp = Blueprint("defense", __name__, url_prefix="/soutenances")


@bp.before_request
@roles_required(ROLE_ADMIN)
def require_admin():
    """Every /soutenances route: admin only."""


def _back(defense_id: int, jury_id: int | None = None):
    return redirect(url_for("defense.detail", defense_id=defense_id) + (f"#jury-{jury_id}" if jury_id else ""))


@bp.route("/", methods=["GET", "POST"])
def index():
    if request.method == "POST":
        try:
            defense = defense_service.save_defense(current_user, request.form)
            flash(f"Soutenance « {defense.name} » programmée : ajoutez ses jurys.", "success")
            return _back(defense.id)
        except BusinessError as exc:
            db.session.rollback()
            flash(str(exc), "danger")
    defenses = db.session.scalars(select(Defense).order_by(Defense.starts_at.desc(), Defense.id.desc())).all()
    return render_template("defense/index.html", defenses=defenses,
                           sas_list=db.session.scalars(select(Sas).order_by(Sas.created_at.desc())).all())


@bp.get("/<int:defense_id>")
def detail(defense_id: int):
    defense = db.get_or_404(Defense, defense_id)
    classes = sorted({c.sas_class for c in defense.sas.members if c.sas_class})
    results = {row.id: defense_service.results(row) for j in defense.juries for row in j.candidates}
    return render_template("defense/detail.html", defense=defense, roles=JURY_ROLES, classes=classes, results=results,
                           staff=defense_service.staff_choices(defense), unassigned=defense_service.unassigned(defense))


@bp.post("/<int:defense_id>")
def update(defense_id: int):
    defense = db.get_or_404(Defense, defense_id)
    try:
        defense_service.save_defense(current_user, request.form, defense)
        flash("Soutenance enregistrée.", "success")
    except BusinessError as exc:
        db.session.rollback()
        flash(str(exc), "danger")
    return _back(defense.id)


@bp.post("/<int:defense_id>/delete")
def delete(defense_id: int):
    defense = db.get_or_404(Defense, defense_id)
    audit_service.log("defense_update", defense, {"deleted": defense.name})
    db.session.delete(defense)
    db.session.commit()
    flash(f"Soutenance « {defense.name} » supprimée.", "success")
    return redirect(url_for("defense.index"))


@bp.post("/<int:defense_id>/juries")
def jury_add(defense_id: int):
    defense = db.get_or_404(Defense, defense_id)
    try:
        jury = defense_service.save_jury(defense, request.form)
        flash(f"Jury « {jury.name} » créé.", "success")
        return _back(defense.id, jury.id)
    except BusinessError as exc:
        db.session.rollback()
        flash(str(exc), "danger")
    return _back(defense.id)


@bp.post("/juries/<int:jury_id>")
def jury_update(jury_id: int):
    jury = db.get_or_404(Jury, jury_id)
    if "delete" in request.form:
        audit_service.log("defense_update", jury.defense, {"jury_deleted": jury.name})
        db.session.delete(jury)
        db.session.commit()
        flash(f"Jury « {jury.name} » supprimé : ses membres et ses étudiants sont libérés.", "success")
        return _back(jury.defense_id)
    try:
        defense_service.save_jury(jury.defense, request.form, jury)
        flash("Jury enregistré.", "success")
    except BusinessError as exc:
        db.session.rollback()
        flash(str(exc), "danger")
    return _back(jury.defense_id, jury.id)


@bp.post("/juries/<int:jury_id>/members")
def member_add(jury_id: int):
    jury = db.get_or_404(Jury, jury_id)
    try:
        member = defense_service.add_member(jury, request.form.get("user_id", type=int), request.form.get("role", ""))
        flash(f"{member.user.display_name} ajouté(e) au jury ({member.role_label}).", "success")
    except BusinessError as exc:
        db.session.rollback()
        flash(str(exc), "danger")
    return _back(jury.defense_id, jury.id)


@bp.post("/members/<int:member_id>")
def member_update(member_id: int):
    member = db.get_or_404(JuryMember, member_id)
    jury = member.jury
    if "remove" in request.form:
        db.session.delete(member)
        flash(f"{member.user.display_name} retiré(e) du jury.", "success")
    elif request.form.get("role") in JURY_ROLES:
        member.role = request.form["role"]
    audit_service.log("defense_update", jury.defense, {"jury": jury.name, "member": member.user.email,
                                                       "role": None if "remove" in request.form else member.role})
    db.session.commit()
    return _back(jury.defense_id, jury.id)


@bp.post("/juries/<int:jury_id>/candidates")
def candidates_load(jury_id: int):
    jury = db.get_or_404(Jury, jury_id)
    added, problems = defense_service.load_candidates(jury, request.form.get("candidates", ""),
                                                      request.form.get("class", ""))
    flash(f"{added} étudiant(s) ajouté(s) au jury « {jury.name} ».", "success" if added else "info")
    if problems:
        flash("Non ajouté(s) : " + " · ".join(problems[:20]) + (" …" if len(problems) > 20 else ""), "warning")
    return _back(jury.defense_id, jury.id)


@bp.post("/jury-candidates/<int:row_id>")
def candidate_update(row_id: int):
    row = db.get_or_404(JuryCandidate, row_id)
    jury_id, defense_id = row.jury_id, row.defense_id
    if "remove" in request.form:
        db.session.delete(row)
        db.session.commit()
    elif request.form.get("move") in ("-1", "1"):
        defense_service.move_candidate(row, int(request.form["move"]))
    return _back(defense_id, jury_id)


@bp.post("/juries/<int:jury_id>/validate")
def jury_validate(jury_id: int):
    """Validate the deliberation (sheets locked, results shown to the students) or reopen it."""
    jury = db.get_or_404(Jury, jury_id)
    reopen = "reopen" in request.form
    defense_service.validate(jury, current_user, reopen=reopen)
    flash(f"Jury « {jury.name} » : délibération " + ("rouverte, les membres peuvent modifier leurs notes."
                                                     if reopen else "validée, résultats publiés."), "success")
    return _back(jury.defense_id, jury.id)
