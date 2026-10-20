import csv
import io
import os
import re
import time
import uuid

import pandas as pd
from flask import (Blueprint, abort, current_app, flash, make_response, redirect, render_template, request, send_file,
                   session, url_for)
from flask_login import current_user, login_user
from sqlalchemy import exists, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload
from werkzeug.utils import secure_filename

from app.auth import _safe_next
from app.extensions import db
from app.models import AttendanceSession, AuditLog, Candidate, KoboSubmission, Room, Sas, TechnicalTestSession, User, Workstation
from app.models.attendance import METHOD_LABELS
from app.models.candidate import FILE_MISSING, FILE_NOT_FOUND, FILE_STATUSES
from app.models.user import ROLE_ADMIN, ROLE_CANDIDATE, ROLES
from app.services import attendance_service, audit_service, candidate_service, import_service, sas_service, settings_service
from app.services.audit_service import ACTION_LABELS
from app.utils import allow_camera, roles_required

bp = Blueprint("admin", __name__, url_prefix="/admin")
MIN_PASSWORD = 8


@bp.before_request
@roles_required(ROLE_ADMIN)
def require_admin():
    """Every /admin route is admin-only (server-side)."""


# ------------------------------------------------------------------ users

@bp.get("/users")
def users():
    if request.args.get("tab") == "candidats":
        return _candidate_accounts()
    staff = select(User).where(User.role != ROLE_CANDIDATE).order_by(User.role, User.email)
    return render_template("admin/users.html", users=db.session.scalars(staff).all(), tab="staff")


def _candidate_accounts():
    """Candidates tab: profiles + logins. Editing/deleting goes through the candidate routes."""
    args = {"tab": "candidats", "q": request.args.get("q", "").strip()}
    stmt = select(Candidate).options(selectinload(Candidate.account)).order_by(Candidate.matricule)
    if args["q"]:
        like = f"%{args['q']}%"
        stmt = stmt.where(or_(Candidate.full_name.ilike(like), Candidate.email.ilike(like),
                              Candidate.matricule.ilike(like),
                              Candidate.exam_code.ilike(like), Candidate.phone.ilike(like)))
    page = db.paginate(stmt, page=request.args.get("page", 1, type=int), per_page=current_app.config["PER_PAGE"],
                       error_out=False)  # last row of the last page deleted: empty page, not a 404
    return render_template("admin/users.html", page=page, args=args, tab="candidats")


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


def _staff_user(user_id: int) -> User:
    """Candidate logins are managed from the candidate file, never from the staff screens."""
    user = db.get_or_404(User, user_id)
    if user.is_candidate:
        abort(404)
    return user


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
    user = _staff_user(user_id)
    if request.method == "POST":
        try:
            password_changed = bool(request.form.get("password"))
            _user_from_form(user, creating=False)
            audit_service.log("user_update", user, {"email": user.email, "role": user.role})
            if password_changed:
                audit_service.log("user_password_reset", user)
            db.session.commit()
            if password_changed and user.id == current_user.id:
                login_user(user)  # own password changed: keep this session, other ones are logged out
            flash("Utilisateur mis à jour.", "success")
            return redirect(url_for("admin.users"))
        except candidate_service.BusinessError as exc:
            db.session.rollback()
            flash(str(exc), "danger")
    return render_template("admin/user_form.html", user=user)


@bp.post("/users/<int:user_id>/toggle")
def user_toggle(user_id: int):
    """Staff and candidate logins alike (a deactivated candidate can no longer log into their space)."""
    user = db.get_or_404(User, user_id)
    back = _safe_next(request.form.get("next")) if request.form.get("next") else url_for("admin.users")
    if user.id == current_user.id:
        flash("Vous ne pouvez pas désactiver votre propre compte.", "danger")
        return redirect(back)
    user.active = not user.active
    audit_service.log("user_activate" if user.active else "user_deactivate", user, {"email": user.email})
    db.session.commit()
    flash(f"Compte {'activé' if user.active else 'désactivé'} : {user.email}.", "success")
    return redirect(back)


# ------------------------------------------------------------------ QR scanner

@bp.route("/scan", methods=["GET", "POST"])
def scan():
    """Decode a candidate's matricule QR code (camera, photo, or a handheld scanner typing into the field)."""
    candidate = matricule = error = None
    if request.method == "POST":
        try:
            matricule = candidate_service.decode_matricule(request.form.get("token", ""))
            candidate = db.session.scalar(select(Candidate).where(Candidate.matricule == matricule))
            if candidate is None:
                error = f"Matricule {matricule} décodé, mais aucun candidat ne le porte."
        except candidate_service.BusinessError as exc:
            error = str(exc)
    response = make_response(render_template("admin/scan.html", c=candidate, matricule=matricule, error=error))
    allow_camera(response)
    return response


# ------------------------------------------------------------------ rooms & workstations

@bp.get("/rooms")
def rooms():
    stmt = select(Room).options(selectinload(Room.workstations)).order_by(Room.name)
    return render_template("admin/rooms.html", rooms=db.session.scalars(stmt).all())


def _apply_upload(apply, *args):
    """Save the uploaded .csv/.xlsx/.xls, run apply(path, *args), always delete the file. None (+ flash) on error."""
    upload = request.files.get("file")
    filename = secure_filename(upload.filename or "") if upload else ""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in current_app.config["ALLOWED_IMPORT_EXTENSIONS"]:
        flash("Format non supporté : utilisez .csv, .xlsx ou .xls.", "danger")
        return None
    path = os.path.join(current_app.config["UPLOAD_FOLDER"], f"{uuid.uuid4().hex}.{ext}")
    upload.save(path)
    try:
        return apply(path, *args)
    except import_service.ImportFileError as exc:
        flash(str(exc), "danger")
        return None
    finally:
        os.remove(path)


@bp.get("/rooms/template")
def rooms_template():
    return _csv_template(import_service.ROOM_COLUMNS, import_service.ROOM_EXAMPLE, "modele_salles.csv")


@bp.post("/rooms/import")
def rooms_import():
    """Rooms + workstations from Excel/CSV: additive, applied directly (nothing is ever deleted)."""
    report = _apply_upload(import_service.apply_rooms)
    if report is None:
        return redirect(url_for("admin.rooms"))
    flash(f"Import des salles : {report['rooms']} salle(s) et {report['workstations']} ordinateur(s) créés, "
          f"{report['updated']} mise(s) à jour, {len(report['errors'])} ligne(s) en erreur.",
          "warning" if report["errors"] else "success")
    stmt = select(Room).options(selectinload(Room.workstations)).order_by(Room.name)
    return render_template("admin/rooms.html", rooms=db.session.scalars(stmt).all(), report=report)


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


# ------------------------------------------------------------------ SAS (next recruitment stage)

def _sas_ready():
    """Both tests validated and not yet in a SAS."""
    return select(Candidate).where(Candidate.motivation_passed, Candidate.technical_passed, Candidate.sas_id.is_(None))


@bp.get("/sas")
def sas_list():
    stmt = select(Sas).options(selectinload(Sas.members)).order_by(Sas.created_at.desc())
    return render_template("admin/sas_list.html", sas_list=db.session.scalars(stmt).all())


@bp.post("/sas/save")
def sas_save():
    sas = db.session.get(Sas, request.form.get("sas_id", type=int) or 0) or Sas()
    sas.name = request.form.get("name", "").strip()[:100]
    sas.description = request.form.get("description", "").strip()[:255]
    if not sas.name:
        flash("Le nom du SAS est obligatoire.", "danger")
        return redirect(url_for("admin.sas_list"))
    db.session.add(sas)
    try:
        db.session.flush()
        audit_service.log("sas_update", sas, {"name": sas.name})
        db.session.commit()
        flash(f"SAS « {sas.name} » enregistré.", "success")
    except IntegrityError:
        db.session.rollback()
        flash("Un SAS porte déjà ce nom.", "danger")
        return redirect(url_for("admin.sas_list"))
    return redirect(url_for("admin.sas_detail", sas_id=sas.id))


@bp.post("/sas/<int:sas_id>/delete")
def sas_delete(sas_id: int):
    sas = db.get_or_404(Sas, sas_id)
    if sas.members:
        flash("Retirez d'abord les candidats de ce SAS.", "danger")
        return redirect(url_for("admin.sas_detail", sas_id=sas.id))
    audit_service.log("sas_update", sas, {"deleted": sas.name})
    db.session.delete(sas)
    db.session.commit()
    flash(f"SAS « {sas.name} » supprimé.", "success")
    return redirect(url_for("admin.sas_list"))


@bp.get("/sas/<int:sas_id>")
def sas_detail(sas_id: int):
    sas = db.get_or_404(Sas, sas_id)
    ready = db.session.scalar(select(func.count()).select_from(_sas_ready().subquery()))
    filters = {k: request.args.get(k, "") for k in ("q", "gender", *sas_service.FIELDS)}
    classes = sorted({c.sas_class for c in sas.members if c.sas_class})
    return render_template("admin/sas.html", sas=sas, ready=ready, filters=filters, classes=classes,
                           members=sas_service.filter_members(sas.members, request.args))


@bp.get("/sas/<int:sas_id>/template")
def sas_template(sas_id: int):
    sas = db.get_or_404(Sas, sas_id)
    rows = [[_neutralize(v) for v in row] for row in sas_service.csv_rows(sas.members)]
    return _csv_template(sas_service.CSV_COLUMNS, rows, f"attributions_sas_{sas.id}.csv")


@bp.post("/sas/<int:sas_id>/import")
def sas_import(sas_id: int):
    sas = db.get_or_404(Sas, sas_id)
    report = _apply_upload(sas_service.apply_csv, sas)
    if report is not None:
        errors = report["errors"]
        flash(f"Import des attributions : {report['updated']} candidat(s) mis à jour, {report['unchanged']} inchangé(s), "
              f"{len(errors)} ligne(s) en erreur.", "warning" if errors else "success")
        if errors:  # redirect (not render): the row forms post back to a GET url
            flash("Lignes ignorées : " + " · ".join(f"ligne {line} : {msg}" for line, msg in errors[:20])
                  + (" …" if len(errors) > 20 else ""), "warning")
    return redirect(url_for("admin.sas_detail", sas_id=sas.id))


@bp.post("/sas/<int:sas_id>/auto-assign")
def sas_auto_assign(sas_id: int):
    """Fill the empty classes / bedrooms / equipment of the members (nothing already given is changed)."""
    sas = db.get_or_404(Sas, sas_id)
    form = request.form
    classes = list(dict.fromkeys(n.strip()[:50] for n in form.get("classes", "").split(",") if n.strip()))
    bedrooms = sas_service.bedroom_names(form.get("bedroom_prefix", "").strip()[:40],
                                         form.get("bedroom_start", 1, type=int) or 0,
                                         form.get("bedroom_count", 0, type=int) or 0)
    stats = sas_service.auto_assign(sas, classes, bedrooms, form.get("capacity", 0, type=int) or 0,
                                    form.get("equipment", "").strip()[:200], form.get("equipment_start", 1, type=int) or 0)
    flash(f"Attribution automatique : {stats['class']} classe(s), {stats['bedroom']} chambre(s), "
          f"{stats['equipment']} matériel(s) attribués.", "success")
    if stats["no_bedroom"]:
        flash(f"{stats['no_bedroom']} candidat(s) sans chambre : plus de place libre (chambres non mixtes). "
              "Ajoutez des chambres ou augmentez le nombre de places.", "warning")
    return redirect(url_for("admin.sas_detail", sas_id=sas.id))


@bp.post("/sas/<int:sas_id>/members")
def sas_admit(sas_id: int):
    """Admit candidates by matricule / email (pasted list), or every candidate who validated both tests.
    Admission marks both tests completed + validated."""
    sas = db.get_or_404(Sas, sas_id)
    unknown = []
    if request.form.get("validated"):
        candidates = db.session.scalars(_sas_ready()).all()
    else:
        keys = {k for k in re.split(r"[\s,;]+", request.form.get("candidates", "")) if k}
        candidates = db.session.scalars(select(Candidate).where(or_(
            Candidate.matricule.in_({k.upper() for k in keys}),
            func.lower(Candidate.email).in_({k.lower() for k in keys})))).all()
        found = {c.matricule for c in candidates} | {(c.email or "").lower() for c in candidates}
        unknown = sorted(k for k in keys if k.upper() not in found and k.lower() not in found)
    admitted = [c for c in candidates if c.sas_id != sas.id]
    for c in admitted:  # ponytail: a candidate already in another SAS is moved (one SAS per candidate)
        candidate_service.admit_to_sas(c, sas, current_user)
    if admitted:
        audit_service.log("sas_admit", sas, {"matricules": [c.matricule for c in admitted]})
    db.session.commit()
    flash(f"{len(admitted)} candidat(s) admis au SAS « {sas.name} ».", "success")
    if unknown:
        flash(f"Introuvable(s) : {', '.join(unknown[:20])}{' …' if len(unknown) > 20 else ''}", "warning")
    return redirect(url_for("admin.sas_detail", sas_id=sas.id))


@bp.post("/sas/<int:sas_id>/members/<int:candidate_id>")
def sas_member(sas_id: int, candidate_id: int):
    """Save class, bedroom + equipment, or remove the candidate from the SAS."""
    c = db.get_or_404(Candidate, candidate_id)
    if c.sas_id != sas_id:
        abort(404)
    if "remove" in request.form:
        c.sas, c.sas_admitted_at, c.sas_class, c.sas_bedroom, c.sas_equipment = None, None, "", "", ""
        audit_service.log("sas_remove", c, {"sas_id": sas_id})
        flash(f"{c.full_name} retiré(e) du SAS.", "success")
    else:
        c.sas_class = request.form.get("class", "").strip()[:50]
        c.sas_bedroom = request.form.get("bedroom", "").strip()[:50]
        c.sas_equipment = request.form.get("equipment", "").strip()[:255]
        audit_service.log("sas_assign", c, {"class": c.sas_class, "bedroom": c.sas_bedroom, "equipment": c.sas_equipment})
        flash(f"Classe, chambre et matériel de {c.full_name} enregistrés.", "success")
    db.session.commit()
    return redirect(_safe_next(request.form.get("next")) if request.form.get("next")
                    else url_for("admin.sas_detail", sas_id=sas_id))  # back to the same filters



# ------------------------------------------------------------------ SAS check-in (pointage)

@bp.post("/sas/<int:sas_id>/attendance")
def attendance_open(sas_id: int):
    sas = db.get_or_404(Sas, sas_id)
    try:
        session = attendance_service.open_session(sas, request.form.get("class_name", ""), request.form.get("label", ""),
                                                  request.form.get("minutes", 0, type=int) or 0, current_user)
    except candidate_service.BusinessError as exc:
        flash(str(exc), "danger")
        return redirect(url_for("admin.sas_detail", sas_id=sas.id))
    return redirect(url_for("admin.attendance", session_id=session.id))


@bp.get("/attendance/<int:session_id>")
def attendance(session_id: int):
    session = db.get_or_404(AttendanceSession, session_id)
    if request.args.get("partial"):
        return _attendance_live(session)
    return allow_camera(make_response(render_template(
        "admin/attendance.html", s=session, **_attendance_context(session))))


def _attendance_context(session: AttendanceSession) -> dict:
    students = attendance_service.roster(session)
    marks = {m.candidate_id: m for m in session.marks}
    return {"students": students, "marks": marks, "present": sum(1 for c in students if c.id in marks),
            "method_labels": METHOD_LABELS, "qr": attendance_service.qr_svg(session) if session.is_open else None}


def _attendance_live(session: AttendanceSession):
    return render_template("admin/_attendance_live.html", s=session, **_attendance_context(session))


@bp.post("/attendance/<int:session_id>/scan")
def attendance_scan(session_id: int):
    """The admin scans the student's matricule QR code (works open or closed)."""
    session = db.get_or_404(AttendanceSession, session_id)
    try:
        candidate, new = attendance_service.admin_scan(session, request.form.get("token", ""), current_user)
        flash(f"{candidate.full_name} : présent(e)." if new else f"{candidate.full_name} était déjà marqué(e) présent(e).",
              "success" if new else "info")
    except candidate_service.BusinessError as exc:
        flash(str(exc), "danger")
    return redirect(url_for("admin.attendance", session_id=session.id))


@bp.post("/attendance/<int:session_id>/mark/<int:candidate_id>")
def attendance_mark(session_id: int, candidate_id: int):
    session = db.get_or_404(AttendanceSession, session_id)
    try:
        attendance_service.admin_set(session, db.get_or_404(Candidate, candidate_id),
                                     request.form.get("present") == "1", current_user)
    except candidate_service.BusinessError as exc:
        flash(str(exc), "danger")
    return redirect(url_for("admin.attendance", session_id=session.id))


@bp.post("/attendance/<int:session_id>/close")
def attendance_close(session_id: int):
    session = db.get_or_404(AttendanceSession, session_id)
    attendance_service.close_session(session)
    flash("Pointage clôturé : les étudiants ne peuvent plus marquer leur présence.", "success")
    return redirect(url_for("admin.attendance", session_id=session.id))


# ------------------------------------------------------------------ imports

def _import_path(key: str = "import") -> str | None:
    info = session.get(key)
    if not info:
        return None
    path = os.path.join(current_app.config["UPLOAD_FOLDER"], info["file"])
    return path if os.path.exists(path) else None


def _purge_stale_uploads(max_age_hours: int = 24) -> None:
    """Abandoned previews are full candidate lists (personal data): don't keep them on disk."""
    folder = current_app.config["UPLOAD_FOLDER"]
    limit = time.time() - max_age_hours * 3600
    for name in os.listdir(folder):
        path = os.path.join(folder, name)
        if os.path.isfile(path) and os.path.getmtime(path) < limit:
            os.remove(path)


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
        _purge_stale_uploads()
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


def _csv_template(columns: list[str], rows: list[list[str]], name: str):
    text = io.StringIO()
    csv.writer(text, delimiter=";", lineterminator="\r\n").writerows([columns, *rows])  # quotes cells holding ';'
    buffer = io.BytesIO(text.getvalue().encode("utf-8-sig"))  # ';' + BOM: Excel FR friendly
    return send_file(buffer, mimetype="text/csv", as_attachment=True, download_name=name)


@bp.get("/imports/results/template")
def results_template():
    return _csv_template(import_service.RESULT_COLUMNS, [import_service.RESULT_EXAMPLE], "modele_resultats_tests.csv")


@bp.post("/imports/results")
def import_results():
    """Validated / not validated tests from a file: stored, then previewed before being applied."""
    upload = request.files.get("file")
    filename = secure_filename(upload.filename or "") if upload else ""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in current_app.config["ALLOWED_IMPORT_EXTENSIONS"]:
        flash("Format non supporté : utilisez .csv, .xlsx ou .xls.", "danger")
        return redirect(url_for("admin.imports"))
    old = _import_path("results_import")
    if old:
        os.remove(old)
    _purge_stale_uploads()
    stored = f"{uuid.uuid4().hex}.{ext}"
    upload.save(os.path.join(current_app.config["UPLOAD_FOLDER"], stored))
    session["results_import"] = {"file": stored, "filename": upload.filename}
    return redirect(url_for("admin.import_results_preview"))


@bp.get("/imports/results/preview")
def import_results_preview():
    path = _import_path("results_import")
    if not path:
        flash("Chargez d'abord un fichier de résultats.", "warning")
        return redirect(url_for("admin.imports"))
    try:
        report = import_service.apply_results(path, current_user._get_current_object(), dry_run=True)
    except import_service.ImportFileError as exc:
        flash(str(exc), "danger")
        return redirect(url_for("admin.imports"))
    return render_template("admin/results_preview.html", report=report,
                           filename=session["results_import"]["filename"])


@bp.post("/imports/results/confirm")
def import_results_confirm():
    path = _import_path("results_import")
    if not path:
        flash("Le fichier de résultats a expiré, rechargez-le.", "warning")
        return redirect(url_for("admin.imports"))
    try:
        report = import_service.apply_results(path, current_user._get_current_object())
    except import_service.ImportFileError as exc:
        flash(str(exc), "danger")
        return redirect(url_for("admin.imports"))
    os.remove(path)
    session.pop("results_import", None)
    return render_template("admin/import_upload.html", report=report)


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
        "Matricule": c.matricule, "N°": c.candidate_number, "Nom": c.full_name, "Email": c.email, "Téléphone": c.phone,
        "Groupe": c.group_name, "Date": c.scheduled_date, "Ordre": c.passage_order, "CNI": c.cni,
        "Kobo": c.kobo_badge[1], "Dossier physique": FILE_STATUSES.get(c.physical_file_status, ""),
        "Numéro pièce": c.piece_number, "Code examen": c.exam_code,
        "Motivation": "Oui" if c.motivation_completed else "Non",
        "Motivation le": fmt(c.motivation_completed_at),
        "Motivation validée": "Oui" if c.motivation_passed else "Non",
        "Salle": s.room.name if s else "", "Ordinateur": s.workstation.name if s else "",
        "Début test": fmt(s.started_at) if s else "", "Fin test": fmt(s.finished_at) if s else "",
        "Durée prévue (min)": s.duration_seconds // 60 if s else "",
        "Durée effective (min)": round(s.elapsed_seconds() / 60, 1) if s else "",
        "Statut test": s.status_badge[1] if s else "",
        "Technique terminé": "Oui" if c.technical_completed else "Non",
        "Technique validé": "Oui" if c.technical_passed else "Non",
        "Statut global": c.global_status,
    }




FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def _neutralize(value):
    """CSV/formula injection: self-registered candidates control names, cities... A text cell starting
    with = + - @ would be executed by Excel; a leading apostrophe makes it plain text."""
    if isinstance(value, str) and value.startswith(FORMULA_PREFIXES):
        return "'" + value
    return value


@bp.get("/exports/<kind>")
def export_download(kind: str):
    if kind not in EXPORTS:
        return redirect(url_for("admin.exports"))
    group = request.args.get("group")
    rows = [{k: _neutralize(v) for k, v in _export_row(c).items()} for c in db.session.scalars(_export_query(kind, group))]
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
