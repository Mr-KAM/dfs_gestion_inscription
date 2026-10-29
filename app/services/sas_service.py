"""SAS members: filters, class / bedroom / equipment from CSV, automatic assignment, admin home figures."""
from collections import Counter, defaultdict

from sqlalchemy import func, select

from app.extensions import db
from app.models import Attendance, AttendanceSession, Exercise, ExerciseSubmission, Incident, Sas
from app.models.campus import REJECTED, SUBMITTED, VALIDATED
from app.services import audit_service
from app.services.import_service import ImportFileError, _clean, read_file
from app.utils import norm_key

# form / filter key -> (candidate attribute, CSV column, max length)
FIELDS = {"class": ("sas_class", "classe", 50), "bedroom": ("sas_bedroom", "chambre", 50),
          "equipment": ("sas_equipment", "materiel", 255)}
CSV_COLUMNS = ["matricule", "email", "nom", "classe", "chambre", "materiel"]
MAX_BEDROOMS = 500


def filter_members(members: list, args) -> list:
    """Search + per-field filter: '' all, 'none' empty, 'set' filled, anything else = exact value.
    ponytail: filtered in Python, a SAS holds a few hundred candidates at most."""
    q = args.get("q", "").strip().lower()
    gender = args.get("gender", "")
    kept = []
    for c in members:
        if q and q not in " ".join((c.full_name, c.matricule, c.email or "", c.exam_code or "")).lower():
            continue
        if gender and (c.gender or "") != gender:
            continue
        if all(_match(getattr(c, attr) or "", args.get(key, "")) for key, (attr, _col, _max) in FIELDS.items()):
            kept.append(c)
    return kept


def _match(value: str, wanted: str) -> bool:
    return {"": True, "none": not value, "set": bool(value)}.get(wanted, value == wanted)


def csv_rows(members: list) -> list[list[str]]:
    """Template = the current members, ready to be filled in and re-imported."""
    return [[c.matricule, c.email or "", c.full_name, c.sas_class or "", c.sas_bedroom or "", c.sas_equipment or ""]
            for c in members]


def apply_csv(path: str, sas) -> dict:
    """Match each line by matricule (else email) among this SAS's members; empty cells leave the value unchanged."""
    df = read_file(path)
    columns = {norm_key(c): c for c in df.columns}
    if not {"matricule", "email"} & columns.keys():
        raise ImportFileError("Colonne « matricule » ou « email » manquante. Utilisez le modèle.")
    by_key = {c.matricule.upper(): c for c in sas.members}
    by_key.update({c.email.lower(): c for c in sas.members if c.email})
    stats = {"updated": 0, "unchanged": 0, "errors": []}
    for index, record in df.iterrows():
        row = {c: _clean(record[columns[c]]) if c in columns else "" for c in CSV_COLUMNS}
        line = index + 2  # header + 1-based lines
        keys = [k for k in (row["matricule"].upper(), row["email"].lower()) if k]
        candidate = next((by_key[k] for k in keys if k in by_key), None)
        if candidate is None:
            stats["errors"].append((line, f"{keys[0] if keys else 'matricule / email vide'} : pas admis dans ce SAS"))
            continue
        too_long = [col for attr, col, limit in FIELDS.values() if len(row[col]) > limit]
        if too_long:
            stats["errors"].append((line, f"trop long : {', '.join(too_long)}"))
            continue
        changed = False
        for attr, col, _limit in FIELDS.values():
            if row[col] and getattr(candidate, attr) != row[col]:
                setattr(candidate, attr, row[col])
                changed = True
        stats["updated" if changed else "unchanged"] += 1
    audit_service.log("sas_assign", sas, {"import": True, "updated": stats["updated"], "errors": len(stats["errors"])})
    db.session.commit()
    return stats


def bedroom_names(prefix: str, start: int, count: int) -> list[str]:
    return [f"{prefix}{n:02d}" for n in range(start, start + min(max(count, 0), MAX_BEDROOMS))]


def auto_assign(sas, classes: list[str], bedrooms: list[str], capacity: int, equipment: str, equipment_start: int,
                ) -> dict:
    """Fill only the empty fields. Classes: balanced sizes and genders. Bedrooms: `capacity` places, never mixed.
    Equipment: `equipment` pattern with {n}, numbered from `equipment_start`, numbers already given are skipped."""
    members = sorted(sas.members, key=lambda c: (c.gender or "", c.full_name))  # genders spread across classes
    stats = {"class": 0, "bedroom": 0, "equipment": 0, "no_bedroom": 0}
    if classes:
        size = Counter({name: 0 for name in classes})
        size.update(c.sas_class for c in members if c.sas_class in size)
        for c in (c for c in members if not c.sas_class):
            c.sas_class = min(classes, key=lambda name: size[name])  # least filled first
            size[c.sas_class] += 1
            stats["class"] += 1
    if bedrooms and capacity > 0:
        occupants = defaultdict(list)
        for c in members:
            if c.sas_bedroom:
                occupants[c.sas_bedroom].append(c.gender or "")
        for c in (c for c in members if not c.sas_bedroom):
            gender = c.gender or ""
            room = next((r for r in bedrooms if len(occupants[r]) < capacity and set(occupants[r]) <= {gender}), None)
            if room is None:
                stats["no_bedroom"] += 1  # no place left: stays empty, reported
                continue
            occupants[room].append(gender)
            c.sas_bedroom = room
            stats["bedroom"] += 1
    if equipment:
        pattern = equipment if "{n}" in equipment else equipment + "{n}"
        used = {c.sas_equipment for c in members if c.sas_equipment}
        numbers = (pattern.replace("{n}", f"{n:02d}") for n in range(max(equipment_start, 0), 10**6))
        free = (label for label in numbers if label not in used)
        for c in sorted((c for c in members if not c.sas_equipment), key=lambda c: (c.sas_class or "", c.full_name)):
            c.sas_equipment = next(free)[:255]
            stats["equipment"] += 1
    audit_service.log("sas_assign", sas, {"auto": True, **stats})
    db.session.commit()
    return stats


def dashboard_stats() -> dict:
    """Admin home: per SAS figures (students, assignments, exercises, roll calls, incidents) and their totals.
    ponytail: loads members / sessions in memory, fine for a few hundred students; group in SQL if it grows."""
    marks = dict(db.session.execute(select(Attendance.session_id, func.count(Attendance.id))
                                    .group_by(Attendance.session_id)).all())
    rows = [sas_figures(sas, marks) for sas in db.session.scalars(select(Sas).order_by(Sas.created_at.desc()))]
    totals = {k: sum(r[k] for r in rows) for k in ("students", "classes", "with_bedroom", "exercises", "to_review",
                                                   "validated", "calls", "open_calls", "absent_hours",
                                                   "open_incidents")}
    return {"rows": rows, "totals": totals}


def sas_figures(sas, marks: dict | None = None) -> dict:
    """Figures of one SAS (admin home row, SAS page tiles). marks: presences by roll call id, loaded if None."""
    sessions = db.session.scalars(select(AttendanceSession).where(AttendanceSession.sas_id == sas.id)).all()
    if marks is None:
        marks = dict(db.session.execute(select(Attendance.session_id, func.count(Attendance.id))
                                        .where(Attendance.session_id.in_([s.id for s in sessions]))
                                        .group_by(Attendance.session_id)).all())
    members = sas.members
    ids = [c.id for c in members]
    per_class = Counter(c.sas_class or "" for c in members)
    # closed roll call -> (students expected, students present)
    closed = []
    for s in sessions:
        if s.is_closed:
            expected = per_class.get(s.class_name, 0) if s.class_name else len(members)
            closed.append((s, expected, min(marks.get(s.id, 0), expected)))
    expected_total = sum(e for _, e, _ in closed)
    subs = Counter(db.session.scalars(select(ExerciseSubmission.status).join(Exercise)
                                      .where(Exercise.sas_id == sas.id)))
    return {
        "sas": sas, "students": len(members),
        "women": sum(1 for c in members if c.gender == "F"), "men": sum(1 for c in members if c.gender == "M"),
        "classes": len([n for n in per_class if n]),
        "with_class": sum(1 for c in members if c.sas_class),
        "with_bedroom": sum(1 for c in members if c.sas_bedroom),
        "with_equipment": sum(1 for c in members if c.sas_equipment),
        "with_photo": sum(1 for c in members if c.photo_type),
        "exercises": db.session.scalar(select(func.count(Exercise.id)).where(Exercise.sas_id == sas.id)) or 0,
        "to_review": subs[SUBMITTED], "validated": subs[VALIDATED], "rejected": subs[REJECTED],
        "calls": len(sessions), "open_calls": sum(1 for s in sessions if s.is_open),
        "absent_hours": sum(s.hours * (e - p) for s, e, p in closed),
        "attendance_rate": round(100 * sum(p for _, _, p in closed) / expected_total) if expected_total else None,
        "open_incidents": (db.session.scalar(select(func.count(Incident.id)).where(
            Incident.candidate_id.in_(ids), Incident.resolved_at.is_(None))) or 0) if ids else 0,
    }
