import io
import os
import uuid

import pandas as pd
from flask import (Blueprint, current_app, flash, redirect, render_template, request, send_file, session,
                   url_for)
from flask_login import current_user
from sqlalchemy import exists, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload
from werkzeug.utils import secure_filename

from app.extensions import db
from app.models import AuditLog, Candidate, KoboSubmission, Room, TechnicalTestSession, User, Workstation
from app.models.candidate import FILE_MISSING, FILE_NOT_FOUND, FILE_STATUSES
from app.models.user import ROLE_ADMIN, ROLES
from app.services import audit_service, candidate_service, import_service, settings_service
from app.services.audit_service import ACTION_LABELS
from app.utils import roles_required

bp = Blueprint("admin", __name__, url_prefix="/admin")
MIN_PASSWORD = 8


@bp.before_request
@roles_required(ROLE_ADMIN)
def require_admin():
    """Every /admin route is admin-only (server-side)."""


# ------------------------------------------------------------------ users

@bp.get("/users")
def users():
    return render_template("admin/users.html", users=db.session.scalars(select(User).order_by(User.role, User.email)).all())


def _user_from_form(user: User, creating: bool) -> None:
    email = request.form.get("email", "").strip().lower()
    role = request.form.get("role", "")
    if "@" not in email:
        raise candidate_service.BusinessError("Email invalide.")
    if role not in ROLES:
        raise candidate_service.BusinessError("Rôle invalide.")
    if user.id == current_user.id and role != ROLE_ADMIN:
        raise candidate_service.BusinessError("Vous ne pouvez pas retirer votre propre rôle administrateur.")
    taken = db.session.scalar(select(User.id).where(func.lower(User.email) == email, User.id != user.id))
    if taken:
        raise candidate_service.BusinessError("Un utilisateur utilise déjà cet email.")
    user.email, user.role = email, role
    user.full_name = request.form.get("full_name", "").strip()[:200]
    password = request.form.get("password", "")
    if creating or password:
        if len(password) < MIN_PASSWORD:
            raise candidate_service.BusinessError(f"Le mot de passe doit contenir au moins {MIN_PASSWORD} caractères.")
        user.set_password(password)


@bp.route("/users/new", methods=["GET", "POST"])
def user_new():
    user = User(role="motivation_tester", active=True)
    if request.method == "POST":
        try:
            _user_from_form(user, creating=True)
            db.session.add(user)
            db.session.flush()
            audit_service.log("user_create", user, {"email": user.email, "role": user.role})
            db.session.commit()
            flash(f"Utilisateur {user.email} créé.", "success")
            return redirect(url_for("admin.users"))
        except (candidate_service.BusinessError, IntegrityError) as exc:
            db.session.rollback()
            flash(str(exc) if isinstance(exc, candidate_service.BusinessError) else "Email déjà utilisé.", "danger")
    return render_template("admin/user_form.html", user=user)


@bp.route("/users/<int:user_id>/edit", methods=["GET", "POST"])
def user_edit(user_id: int):
    user = db.get_or_404(User, user_id)
    if request.method == "POST":
        try:
            password_changed = bool(request.form.get("password"))
            _user_from_form(user, creating=False)
            audit_service.log("user_update", user, {"email": user.email, "role": user.role})
            if password_changed:
                audit_service.log("user_password_reset", user)
            db.session.commit()
            flash("Utilisateur mis à jour.", "success")
            return redirect(url_for("admin.users"))
        except candidate_service.BusinessError as exc:
            db.session.rollback()
            flash(str(exc), "danger")
    return render_template("admin/user_form.html", user=user)


@bp.post("/users/<int:user_id>/toggle")
def user_toggle(user_id: int):
    user = db.get_or_404(User, user_id)
    if user.id == current_user.id:
        flash("Vous ne pouvez pas désactiver votre propre compte.", "danger")
        return redirect(url_for("admin.users"))
    user.active = not user.active
    audit_service.log("user_activate" if user.active else "user_deactivate", user, {"email": user.email})
    db.session.commit()
    flash(f"Compte {'activé' if user.active else 'désactivé'} : {user.email}.", "success")
    return redirect(url_for("admin.users"))


# ------------------------------------------------------------------ rooms & workstations

@bp.get("/rooms")
def rooms():
    stmt = select(Room).options(selectinload(Room.workstations)).order_by(Room.name)
    return render_template("admin/rooms.html", rooms=db.session.scalars(stmt).all())


@bp.post("/rooms/save")
def room_save():
    room = db.session.get(Room, request.form.get("room_id", type=int) or 0) or Room()
    room.name = request.form.get("name", "").strip()[:100]
    room.description = request.form.get("description", "").strip()[:255]
    if not room.name:
        flash("Le nom de la salle est obligatoire.", "danger")
        return redirect(url_for("admin.rooms"))
    db.session.add(room)
    try:
        db.session.flush()
        audit_service.log("room_update", room, {"name": room.name})
        db.session.commit()
        flash(f"Salle « {room.name} » enregistrée.", "success")
    except IntegrityError:
        db.session.rollback()
        flash("Une salle porte déjà ce nom.", "danger")
    return redirect(url_for("admin.rooms"))


@bp.post("/rooms/<int:room_id>/toggle")
def room_toggle(room_id: int):
    room = db.get_or_404(Room, room_id)
    room.active = not room.active
    audit_service.log("room_update", room, {"active": room.active})
    db.session.commit()
    return redirect(url_for("admin.rooms"))


@bp.post("/rooms/<int:room_id>/workstations")
def workstations_add(room_id: int):
    """Bulk-create PC-01..PC-NN (existing numbers are skipped)."""
    room = db.get_or_404(Room, room_id)
    prefix = request.form.get("prefix", "PC-").strip()[:20]
    start = request.form.get("start", 1, type=int)
    count = min(request.form.get("count", 1, type=int), 200)
    existing = {w.computer_number for w in room.workstations}
    created = 0
    for n in range(start, start + max(count, 0)):
        number = f"{prefix}{n:02d}"
        if number not in existing:
            room.workstations.append(Workstation(computer_number=number))
            created += 1
    audit_service.log("room_update", room, {"workstations_added": created})
    db.session.commit()
    flash(f"{created} ordinateur(s) ajouté(s) à {room.name}.", "success")
    return redirect(url_for("admin.rooms"))


@bp.post("/workstations/<int:ws_id>/save")
def workstation_save(ws_id: int):
    ws = db.get_or_404(Workstation, ws_id)
    if "toggle" in request.form:
        busy = db.session.scalar(select(exists().where(
            TechnicalTestSession.workstation_id == ws.id, TechnicalTestSession.status.in_(("running", "paused")))))
        if busy and ws.active:
            flash("Cet ordinateur est utilisé par un test en cours.", "danger")
            return redirect(url_for("admin.rooms"))
        ws.active = not ws.active
    else:
        ws.label = request.form.get("label", "").strip()[:100]
    audit_service.log("room_update", ws, {"active": ws.active, "label": ws.label})
    db.session.commit()
    return redirect(url_for("admin.rooms"))


# ------------------------------------------------------------------ imports

def _import_path() -> str | None:
    info = session.get("import")
    if not info:
        return None
    path = os.path.join(current_app.config["UPLOAD_FOLDER"], info["file"])
    return path if os.path.exists(path) else None


def _mapping_from_form() -> dict[str, str]:
    return {f: request.form.get(f"map_{f}", "") for f in import_service.FIELDS if request.form.get(f"map_{f}")}


@bp.route("/imports", methods=["GET", "POST"])
def imports():
    if request.method == "POST" and "file" in request.files:
        upload = request.files["file"]
        filename = secure_filename(upload.filename or "")
        ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        if ext not in current_app.config["ALLOWED_IMPORT_EXTENSIONS"]:
            flash("Format non supporté : utilisez .csv, .xlsx ou .xls.", "danger")
            return redirect(url_for("admin.imports"))
        old = _import_path()
        if old:
            os.remove(old)
        stored = f"{uuid.uuid4().hex}.{ext}"
        upload.save(os.path.join(current_app.config["UPLOAD_FOLDER"], stored))
        session["import"] = {"file": stored, "filename": upload.filename}
        return redirect(url_for("admin.import_preview"))
    return render_template("admin/import_upload.html")


@bp.route("/imports/preview", methods=["GET", "POST"])
def import_preview():
    path = _import_path()
    if not path:
        flash("Chargez d'abord un fichier.", "warning")
        return redirect(url_for("admin.imports"))
    try:
        result = import_service.preview(path, _mapping_from_form() if request.method == "POST" else None)
    except import_service.ImportFileError as exc:
        flash(str(exc), "danger")
        return redirect(url_for("admin.imports"))
    return render_template("admin/import_preview.html", r=result, filename=session["import"]["filename"],
                           fields=import_service.FIELDS, actions=import_service.ACTIONS)


@bp.post("/imports/confirm")
def import_confirm():
    path = _import_path()
    if not path:
        flash("Le fichier d'import a expiré, rechargez-le.", "warning")
        return redirect(url_for("admin.imports"))
    # One select per duplicate row (action_<line>); missing ones default to "ignore".
    actions = {int(k[7:]): v for k, v in request.form.items()
               if k.startswith("action_") and k[7:].isdigit() and v in import_service.ACTIONS}
    try:
        stats = import_service.apply(path, _mapping_from_form(), actions, session["import"]["filename"])
    except import_service.ImportFileError as exc:
        flash(str(exc), "danger")
        return redirect(url_for("admin.import_preview"))
    os.remove(path)
    session.pop("import", None)
    return render_template("admin/import_result.html", stats=stats)


# ------------------------------------------------------------------ exports

EXPORTS = {
    "all": "Tous les candidats",
    "motivation": "Motivation terminée",
    "technical": "Tests techniques",
    "group": "Candidats par groupe",
    "absent": "Candidats absents",
    "nonconform": "Dossiers non conformes",
}


@bp.get("/exports")
def exports():
    return render_template("admin/exports.html", exports=EXPORTS, groups=candidate_service.distinct_groups())


def _export_query(kind: str, group: str | None):
    stmt = select(Candidate).options(
        selectinload(Candidate.sessions).selectinload(TechnicalTestSession.room),
        selectinload(Candidate.sessions).selectinload(TechnicalTestSession.workstation),
    ).order_by(Candidate.group_name, Candidate.passage_order, Candidate.full_name)
    if kind == "motivation":
        stmt = stmt.where(Candidate.motivation_completed.is_(True))
    elif kind == "technical":
        stmt = stmt.where(Candidate.sessions.any())
    elif kind == "group" and group:
        stmt = stmt.where(Candidate.group_name == group)
    elif kind == "absent":
        # Scheduled on or before today, never seen: no Kobo file and no motivation interview.
        stmt = stmt.where(
            Candidate.scheduled_date <= candidate_service.today(), Candidate.motivation_completed.is_(False),
            ~exists().where(KoboSubmission.candidate_id == Candidate.id),
        )
    elif kind == "nonconform":
        stmt = stmt.where(Candidate.physical_file_status.in_((FILE_MISSING, FILE_NOT_FOUND)))
    return stmt


def _export_row(c: Candidate) -> dict:
    s = c.latest_session
    fmt = current_app.jinja_env.filters["dt"]
    return {
        "N°": c.candidate_number, "Nom": c.full_name, "Email": c.email, "Téléphone": c.phone,
        "Groupe": c.group_name, "Date": c.scheduled_date, "Ordre": c.passage_order, "CNI": c.cni,
        "Kobo": c.kobo_badge[1], "Dossier physique": FILE_STATUSES.get(c.physical_file_status, ""),
        "Numéro pièce": c.piece_number, "Code examen": c.exam_code,
        "Motivation": "Oui" if c.motivation_completed else "Non",
        "Motivation le": fmt(c.motivation_completed_at),
        "Salle": s.room.name if s else "", "Ordinateur": s.workstation.name if s else "",
        "Début test": fmt(s.started_at) if s else "", "Fin test": fmt(s.finished_at) if s else "",
        "Durée prévue (min)": s.duration_seconds // 60 if s else "",
        "Durée effective (min)": round(s.elapsed_seconds() / 60, 1) if s else "",
        "Statut test": s.status_badge[1] if s else "",
        "Technique terminé": "Oui" if c.technical_completed else "Non",
        "Statut global": c.global_status,
    }


@bp.get("/exports/<kind>")
def export_download(kind: str):
    if kind not in EXPORTS:
        return redirect(url_for("admin.exports"))
    group = request.args.get("group")
    rows = [_export_row(c) for c in db.session.scalars(_export_query(kind, group))]
    df = pd.DataFrame(rows)
    fmt = "xlsx" if request.args.get("format") == "xlsx" else "csv"
    audit_service.log("export", details={"kind": kind, "group": group, "format": fmt, "rows": len(rows)})
    db.session.commit()
    buffer = io.BytesIO()
    name = f"dfs_{kind}{'_' + secure_filename(group) if group else ''}.{fmt}"
    if fmt == "xlsx":
        df.to_excel(buffer, index=False, sheet_name="Export")
        mimetype = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    else:
        buffer.write(df.to_csv(index=False, sep=";").encode("utf-8-sig"))  # ';' + BOM: Excel FR friendly
        mimetype = "text/csv"
    buffer.seek(0)
    return send_file(buffer, mimetype=mimetype, as_attachment=True, download_name=name)


# ------------------------------------------------------------------ audit & settings

@bp.get("/audit")
def audit():
    stmt = select(AuditLog).options(selectinload(AuditLog.user)).order_by(AuditLog.created_at.desc())
    action = request.args.get("action")
    if action:
        stmt = stmt.where(AuditLog.action == action)
    page = db.paginate(stmt, page=request.args.get("page", 1, type=int), per_page=50)
    return render_template("admin/audit.html", page=page, labels=ACTION_LABELS, action=action)


SETTINGS_FIELDS = ["campaign_name", "campaign_year", "exam_code_prefix", "exam_code_format",
                   "technical_duration", "app_name", "timezone"]


@bp.route("/settings", methods=["GET", "POST"])
def settings():
    if request.method == "POST":
        values = {k: request.form.get(k, "").strip() for k in SETTINGS_FIELDS}
        error = _validate_settings(values)
        if error:
            flash(error, "danger")
        else:
            settings_service.set_many(values)
            audit_service.log("settings_update", details=values)
            db.session.commit()
            flash("Paramètres enregistrés.", "success")
            return redirect(url_for("admin.settings"))
    values = {k: settings_service.get(k) for k in SETTINGS_FIELDS}
    return render_template(
        "admin/settings.html", v=values, kobo_url=settings_service.get("kobo_base_url"),
        kobo_uid=settings_service.get("kobo_asset_uid"), kobo_token_set=bool(settings_service.get("kobo_token")),
        kobo_last_sync=settings_service.get("kobo_last_sync"), next_code=candidate_service.next_exam_code(),
    )


def _validate_settings(values: dict) -> str | None:
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    if not values["technical_duration"].isdigit() or not 1 <= int(values["technical_duration"]) <= 480:
        return "La durée du test doit être un nombre de minutes entre 1 et 480."
    if "{seq" not in values["exam_code_format"]:
        return "Le format du code examen doit contenir {seq} (ex. {prefix}-{year}-{seq:04d})."
    try:
        values["exam_code_format"].format(prefix="X", year="2026", seq=1)
    except (KeyError, ValueError, IndexError):
        return "Format de code examen invalide. Variables autorisées : {prefix}, {year}, {seq:04d}."
    try:
        ZoneInfo(values["timezone"])
    except (ZoneInfoNotFoundError, ValueError):
        return "Fuseau horaire inconnu (ex. Africa/Abidjan)."
    return None
