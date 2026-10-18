"""Candidate import from CSV / Excel: parse, map columns, validate, dedupe, apply."""
import re
from dataclasses import dataclass, field
from datetime import date, timedelta

import pandas as pd
from sqlalchemy import func, select

from app.extensions import db
from app.models import Candidate, Room, TechnicalTestSession
from app.models.technical_test import STATUS_COMPLETED
from app.services import account_service, audit_service
from app.services.candidate_service import EXAM_CODE_RE, normalize_exam_code
from app.services.timer_service import MAX_DURATION_MINUTES
from app.utils import norm_cni, norm_email, norm_key, norm_name, norm_phone, utcnow

# field -> (label, normalised header aliases)
FIELDS = {
    "candidate_number": ("N°", ["n", "no", "num", "numero", "ndeg", "nordre", "id"]),
    "group_name": ("Groupe", ["groupe", "group", "grp"]),
    "scheduled_date": ("Date", ["date", "datepassage", "datedepassage", "jour"]),
    "scheduled_time": ("Heure", ["heure", "horaire", "heurepassage", "time"]),
    "passage_order": ("Ordre", ["ordre", "ordrepassage", "ordredepassage", "rang"]),
    "full_name": ("Nom et prénoms", ["nometprenoms", "nometprenom", "nomprenoms", "nomprenom", "nomsetprenoms",
                                     "nomcomplet", "fullname", "nom"]),
    "birth_date": ("Date de naissance", ["datedenaissance", "datenaissance", "naissance", "birthdate"]),
    "gender": ("Sexe", ["sexe", "genre", "gender"]),
    "email": ("Email", ["email", "mail", "adresseemail", "adressemail", "courriel"]),
    "phone": ("Téléphone", ["telephone", "tel", "phone", "contact", "numerotelephone", "numerodetelephone"]),
    "city": ("Ville", ["ville", "commune", "city", "localite"]),
    "cni": ("CNI", ["cni", "ncni", "numerocni", "cnib", "pieceidentite"]),
}
REQUIRED_FIELDS = {"full_name"}
ACTIONS = {"ignore": "Ignorer", "update": "Mettre à jour", "review": "Examiner"}
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class ImportFileError(Exception):
    pass


@dataclass
class ImportRow:
    line: int
    values: dict
    errors: list[str] = field(default_factory=list)
    duplicate_of: Candidate | None = None
    duplicate_reason: str = ""

    @property
    def valid(self) -> bool:
        return not self.errors


def read_file(path: str) -> pd.DataFrame:
    try:
        if path.lower().endswith(".csv"):
            try:
                df = pd.read_csv(path, dtype=str, sep=None, engine="python", encoding="utf-8-sig")
            except UnicodeDecodeError:
                df = pd.read_csv(path, dtype=str, sep=None, engine="python", encoding="latin-1")
        else:
            df = pd.read_excel(path, dtype=str)
    except Exception as exc:  # pandas raises many types for corrupt files
        raise ImportFileError(f"Fichier illisible : {exc}") from exc
    df = df.dropna(how="all").fillna("")
    df.columns = [str(c).strip() for c in df.columns]
    return df


def auto_mapping(columns: list[str]) -> dict[str, str]:
    """field -> column. Exact alias match first, so 'Nom et prénoms' beats 'Nom'."""
    mapping: dict[str, str] = {}
    used: set[str] = set()
    keys = {col: norm_key(col) for col in columns}
    for fname, (_label, aliases) in FIELDS.items():
        for alias in aliases:
            col = next((c for c, k in keys.items() if k == alias and c not in used), None)
            if col:
                mapping[fname] = col
                used.add(col)
                break
    return mapping


def _clean(value) -> str:
    text = str(value or "").strip()
    if text.lower() in {"nan", "nat", "none"}:
        return ""
    return text[:-2] if re.fullmatch(r"\d+\.0", text) else text  # Excel floats: "707070707.0"


def _parse_date(value: str) -> tuple[date | None, bool]:
    """Returns (date, ok). Accepts dd/mm/yyyy, ISO and Excel datetime strings."""
    if not value:
        return None, True
    iso = re.match(r"^(\d{4})-(\d{2})-(\d{2})", value)
    try:
        if iso:
            return date(int(iso[1]), int(iso[2]), int(iso[3])), True
        if re.fullmatch(r"\d{5}", value):  # Excel serial number
            return (pd.Timestamp("1899-12-30") + pd.Timedelta(days=int(value))).date(), True
        parsed = pd.to_datetime(value, dayfirst=True, errors="coerce")
        return (None, False) if pd.isna(parsed) else (parsed.date(), True)
    except (ValueError, OverflowError):
        return None, False


def _gender(value: str) -> str:
    key = norm_key(value)
    if key in {"m", "h", "masculin", "homme", "male", "garcon"}:
        return "M"
    if key in {"f", "feminin", "femme", "female", "fille"}:
        return "F"
    return ""


def build_rows(df: pd.DataFrame, mapping: dict[str, str]) -> list[ImportRow]:
    rows = []
    for idx, record in enumerate(df.to_dict("records")):
        raw = {f: _clean(record.get(col, "")) for f, col in mapping.items() if col}
        row = ImportRow(line=idx + 2, values={})  # +2: header line + 1-based
        v = row.values
        v["candidate_number"] = raw.get("candidate_number", "")[:50]
        v["group_name"] = raw.get("group_name", "")[:50]
        v["scheduled_time"] = raw.get("scheduled_time", "")[:30]
        if re.fullmatch(r"\d{2}:\d{2}:\d{2}", v["scheduled_time"]):
            v["scheduled_time"] = v["scheduled_time"][:5]
        v["full_name"] = " ".join(raw.get("full_name", "").split())[:200]
        v["email"] = norm_email(raw.get("email"))[:255]
        v["phone"] = norm_phone(raw.get("phone")) or raw.get("phone", "")[:30]
        v["city"] = raw.get("city", "")[:120]
        v["cni"] = raw.get("cni", "").strip().upper()[:50]
        v["gender"] = _gender(raw.get("gender", ""))

        for fname in ("scheduled_date", "birth_date"):
            v[fname], ok = _parse_date(raw.get(fname, ""))
            if not ok:
                row.errors.append(f"{FIELDS[fname][0]} invalide : « {raw[fname]} »")
        order = raw.get("passage_order", "")
        v["passage_order"] = int(order) if order.isdigit() else None
        if order and not order.isdigit():
            row.errors.append(f"Ordre invalide : « {order} »")
        if not v["full_name"]:
            row.errors.append("Nom et prénoms manquant")
        if v["email"] and not EMAIL_RE.match(v["email"]):
            row.errors.append(f"Email invalide : « {v['email']} »")
        if raw.get("gender") and not v["gender"]:
            row.errors.append(f"Sexe non reconnu : « {raw['gender']} »")
        rows.append(row)
    return rows


def _identity_keys(values: dict) -> list[tuple[str, str]]:
    """Duplicate keys in priority order (spec §9): email, phone, CNI, name + birth date."""
    keys = []
    if values.get("email"):
        keys.append(("email", norm_email(values["email"])))
    if values.get("phone"):
        keys.append(("téléphone", norm_phone(values["phone"])))
    if values.get("cni"):
        keys.append(("CNI", norm_cni(values["cni"])))
    if values.get("full_name") and values.get("birth_date"):
        keys.append(("nom + date de naissance", f"{norm_name(values['full_name'])}|{values['birth_date']}"))
    return keys


def detect_duplicates(rows: list[ImportRow]) -> None:
    # ponytail: in-memory index of every candidate, fine for a few thousand rows
    index: dict[tuple[str, str], Candidate] = {}
    for c in db.session.scalars(select(Candidate)):
        for key in _identity_keys({"email": c.email, "phone": c.phone, "cni": c.cni,
                                   "full_name": c.full_name, "birth_date": c.birth_date}):
            index.setdefault(key, c)
    seen_in_file: dict[tuple[str, str], int] = {}
    for row in rows:
        for key in _identity_keys(row.values):
            if row.duplicate_of is None and key in index:
                row.duplicate_of, row.duplicate_reason = index[key], key[0]
            if key in seen_in_file and not any(e.startswith("Doublon dans le fichier") for e in row.errors):
                row.errors.append(f"Doublon dans le fichier ({key[0]}, ligne {seen_in_file[key]})")
            seen_in_file.setdefault(key, row.line)


def preview(path: str, mapping: dict[str, str] | None = None) -> dict:
    df = read_file(path)
    columns = list(df.columns)
    mapping = {f: c for f, c in (mapping or auto_mapping(columns)).items() if c in columns}
    missing = [FIELDS[f][0] for f in REQUIRED_FIELDS if f not in mapping]
    rows = build_rows(df, mapping) if not missing else []
    detect_duplicates(rows)
    return {
        "columns": columns,
        "mapping": mapping,
        "missing": missing,
        "rows": rows,
        "total": len(df),
        "valid": sum(1 for r in rows if r.valid and not r.duplicate_of),
        "errors": sum(1 for r in rows if not r.valid),
        "duplicates": sum(1 for r in rows if r.duplicate_of and r.valid),
    }


UPDATABLE = ["candidate_number", "group_name", "scheduled_date", "scheduled_time", "passage_order",
             "full_name", "birth_date", "gender", "email", "phone", "city", "cni"]


def apply(path: str, mapping: dict[str, str], actions: dict[int, str], filename: str) -> dict:
    """Insert valid new rows; for duplicates apply the chosen action (default: ignore)."""
    result = preview(path, mapping)
    if result["missing"]:
        raise ImportFileError("Colonnes obligatoires absentes : " + ", ".join(result["missing"]))
    stats = {"created": 0, "updated": 0, "ignored": 0, "errors": 0, "review": []}
    for row in result["rows"]:
        if not row.valid:
            stats["errors"] += 1
            continue
        if row.duplicate_of is None:
            db.session.add(Candidate(**{k: (v or None) for k, v in row.values.items()}))
            stats["created"] += 1
            continue
        action = actions.get(row.line, "ignore")
        if action == "update":
            for key in UPDATABLE:
                if row.values.get(key) not in ("", None):  # never blank existing data
                    setattr(row.duplicate_of, key, row.values[key])
            stats["updated"] += 1
        elif action == "review":
            stats["review"].append(row)
        else:
            stats["ignored"] += 1
    audit_service.log("candidate_import", details={
        "file": filename, "created": stats["created"], "updated": stats["updated"],
        "ignored": stats["ignored"], "errors": stats["errors"], "review": len(stats["review"]),
    })
    db.session.commit()
    stats["accounts"] = account_service.ensure_candidate_accounts()  # login: email + initial password
    return stats


# ---------------------------------------------------------------- test results

RESULT_COLUMNS = ["email", "code_examen", "motivation_status", "technical_status", "salle", "ordi", "time"]
RESULT_EXAMPLE = ["aya@exemple.ci", "DFS-2026-0001", "oui", "oui", "Salle A", "PC-01", "45"]
YES_NO = {"oui": True, "o": True, "yes": True, "1": True, "non": False, "n": False, "no": False, "0": False}


def _yes_no(value: str, label: str):
    """'' -> None (left unchanged), oui/non -> bool."""
    if not value:
        return None
    if value.lower() not in YES_NO:
        raise ImportFileError(f"{label} : « {value} » doit être oui ou non")
    return YES_NO[value.lower()]


def _result_session(candidate: Candidate, row: dict, admin) -> TechnicalTestSession | None:
    """A completed session for a technical test passed outside the app's chronometer.
    No salle/ordi/time at all -> no session (the test is only marked completed)."""
    if candidate.active_session:
        raise ImportFileError("test technique en cours dans l'application : terminez-le d'abord")
    given = [row["salle"], row["ordi"], row["time"]]
    if not any(given):
        return None
    if not all(given):
        raise ImportFileError("salle, ordi et time : renseignez les trois ou aucun")
    room = db.session.scalar(select(Room).where(func.lower(Room.name) == row["salle"].lower()))
    if room is None:
        raise ImportFileError(f"salle « {row['salle']} » inconnue")
    ordi = row["ordi"].lower()
    workstation = next((w for w in room.workstations if ordi in (w.computer_number.lower(), (w.label or "").lower())), None)
    if workstation is None:
        raise ImportFileError(f"ordi « {row['ordi']} » inconnu dans {room.name}")
    if not row["time"].isdigit() or not 1 <= int(row["time"]) <= MAX_DURATION_MINUTES:
        raise ImportFileError(f"time : durée en minutes entre 1 et {MAX_DURATION_MINUTES} attendue")
    # ponytail: real start time unknown, the session is dated "now" with the given duration.
    seconds, now = int(row["time"]) * 60, utcnow()
    return TechnicalTestSession(
        candidate=candidate, technical_tester_id=admin.id, room_id=room.id, workstation_id=workstation.id,
        duration_seconds=seconds, started_at=now - timedelta(seconds=seconds), finished_at=now,
        total_pause_seconds=0, status=STATUS_COMPLETED,
    )


def _find_candidate(email: str) -> Candidate:
    email = norm_email(email)
    if not email:
        raise ImportFileError("email obligatoire")
    found = db.session.scalars(select(Candidate).where(func.lower(func.trim(Candidate.email)) == email).limit(2)).all()
    if not found:
        raise ImportFileError(f"aucun candidat avec l'email {email}")
    if len(found) > 1:
        raise ImportFileError(f"plusieurs candidats ont l'email {email}, à corriger dans la fiche candidat")
    return found[0]


def _apply_result(row: dict, admin) -> tuple[Candidate, list[str]]:
    """Batch validation: the candidate gets the file's exam code if he has none, motivation and technical
    tests are marked completed, then the oui/non verdicts are recorded."""
    candidate = _find_candidate(row["email"])
    code = normalize_exam_code(row["code_examen"])
    motivation = _yes_no(row["motivation_status"], "motivation_status")
    technical = _yes_no(row["technical_status"], "technical_status")
    if candidate.exam_code and code and code != candidate.exam_code:
        raise ImportFileError(f"le candidat a déjà le code examen {candidate.exam_code} (différent de {code})")
    if not candidate.exam_code:
        if not code:
            raise ImportFileError("code_examen obligatoire : le candidat n'a pas encore de code examen")
        if not EXAM_CODE_RE.match(code):
            raise ImportFileError("code examen invalide : lettres, chiffres et - _ / . uniquement (50 max)")
        other = db.session.scalar(select(Candidate).where(Candidate.exam_code == code))
        if other:
            raise ImportFileError(f"code examen {code} déjà attribué à {other.full_name}")
    session = None
    if technical is not None and not candidate.technical_completed:
        session = _result_session(candidate, row, admin)

    # All checks passed: apply.
    notes, now = [], utcnow()
    if not candidate.exam_code:
        candidate.exam_code = code
        notes.append("code examen attribué")
        audit_service.log("exam_code_create", candidate, {"exam_code": code, "generated": False, "source": "import"})
    if not candidate.motivation_completed:
        candidate.motivation_completed, candidate.motivation_completed_at = True, now
        candidate.motivation_completed_by = admin.id
        notes.append("motivation terminée")
    if technical is not None and not candidate.technical_completed:
        if session:
            db.session.add(session)
        candidate.technical_completed, candidate.technical_completed_at = True, now
        notes.append("test technique terminé")
    if motivation is not None:
        candidate.motivation_passed = motivation
    if technical is not None:
        candidate.technical_passed = technical
    audit_service.log("results_import", candidate, {k: v for k, v in row.items() if v})
    return candidate, notes


def apply_results(path: str, admin, dry_run: bool = False) -> dict:
    """Validated / not validated tests per candidate. Bad lines are skipped and reported, good ones applied.
    dry_run: same checks in sequence (duplicate lines included), then rolled back -> the preview."""
    df = read_file(path)
    columns = {norm_key(c): c for c in df.columns}
    missing = [c for c in RESULT_COLUMNS if norm_key(c) not in columns]
    if missing:
        raise ImportFileError(f"Colonnes manquantes : {', '.join(missing)}. Utilisez le modèle CSV.")
    stats = {"updated": 0, "errors": [], "rows": []}
    for index, record in df.iterrows():
        row = {c: _clean(record[columns[norm_key(c)]]) for c in RESULT_COLUMNS}
        line, candidate, error, notes = index + 2, None, None, []  # +2: header + 1-based lines
        try:
            candidate, notes = _apply_result(row, admin)
            stats["updated"] += 1
        except ImportFileError as exc:
            error = str(exc)
            stats["errors"].append((line, row["email"], error))
        stats["rows"].append({"line": line, "values": row, "name": candidate.full_name if candidate else "",
                              "notes": notes, "error": error})
    if dry_run:
        db.session.rollback()
        return stats
    audit_service.log("results_import", None, {"updated": stats["updated"], "errors": len(stats["errors"])})
    db.session.commit()
    return stats
