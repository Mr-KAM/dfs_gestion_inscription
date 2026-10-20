"""Candidate accounts: bulk creation with the default password, self sign-up, password change."""
import io
import re
from datetime import date

from flask import abort, current_app, send_file
from sqlalchemy import exists, func, or_, select
from werkzeug.security import generate_password_hash

from app.extensions import db
from app.models import Candidate, User
from app.models.user import ROLE_CANDIDATE
from app.services import audit_service
from app.services.candidate_service import BusinessError
from app.utils import norm_email, norm_phone

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MIN_PASSWORD = 8
PHOTO_MAX_BYTES = 2 * 1024 * 1024


def _new_account(candidate: Candidate, email: str, password_hash: str, must_change: bool) -> User:
    return User(email=email, full_name=candidate.full_name, role=ROLE_CANDIDATE, candidate=candidate,
                password_hash=password_hash, must_change_password=must_change, active=True)


def ensure_candidate_accounts() -> dict:
    """Give every candidate with a valid email a login (email + default password). Idempotent.

    Candidates without a usable email, or whose email is already used by another account, are
    skipped and reported: one email can only log into one account.
    """
    users_by_email = {u.email: u for u in db.session.scalars(select(User))}
    candidates = db.session.scalars(
        select(Candidate).where(~exists().where(User.candidate_id == Candidate.id)).order_by(Candidate.id)
    ).all()
    default_hash = None  # ponytail: one hash for all default accounts (same known password anyway)
    created, no_email, conflicts = 0, [], []
    for candidate in candidates:
        email = norm_email(candidate.email)
        if not EMAIL_RE.match(email):
            no_email.append(candidate)
            continue
        if email in users_by_email:
            conflicts.append(candidate)
            continue
        default_hash = default_hash or generate_password_hash(current_app.config["CANDIDATE_DEFAULT_PASSWORD"])
        users_by_email[email] = _new_account(candidate, email, default_hash, must_change=True)
        db.session.add(users_by_email[email])
        created += 1
    if created:
        audit_service.log("candidate_accounts_create", details={"created": created})
    db.session.commit()
    return {"created": created, "no_email": no_email, "conflicts": conflicts}


def sync_account(candidate: Candidate) -> None:
    """After an admin creates/edits a candidate: create the login, or follow an email change."""
    email = norm_email(candidate.email)
    account = candidate.account
    if not EMAIL_RE.match(email):
        if account:
            raise BusinessError("Ce candidat a un compte de connexion : son email ne peut pas être vidé.")
        return
    owner = db.session.scalar(select(User).where(func.lower(User.email) == email))
    if owner and owner is not account:
        raise BusinessError("Cet email est déjà utilisé par un autre compte de connexion.")
    if account:
        account.email, account.full_name = email, candidate.full_name
    else:
        default_hash = generate_password_hash(current_app.config["CANDIDATE_DEFAULT_PASSWORD"])
        db.session.add(_new_account(candidate, email, default_hash, must_change=True))


def reset_password(account: User) -> None:
    account.set_password(current_app.config["CANDIDATE_DEFAULT_PASSWORD"])
    account.must_change_password = True
    audit_service.log("user_password_reset", account, {"email": account.email})
    db.session.commit()


def _parse_birth_date(value: str) -> date | None:
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        raise BusinessError("Date de naissance invalide.")


def check_new_password(password: str, confirm: str) -> None:
    if len(password) < MIN_PASSWORD:
        raise BusinessError(f"Le mot de passe doit contenir au moins {MIN_PASSWORD} caractères.")
    if password != confirm:
        raise BusinessError("Les deux mots de passe ne correspondent pas.")
    if password == current_app.config["CANDIDATE_DEFAULT_PASSWORD"]:
        raise BusinessError("Choisissez un mot de passe différent du mot de passe initial.")


def register(form) -> User:
    """Self sign-up: creates the candidate record and its login. Never duplicates a candidate."""
    full_name = " ".join(form.get("full_name", "").split())[:200]
    email = norm_email(form.get("email"))[:255]
    phone = norm_phone(form.get("phone"))
    cni = form.get("cni", "").strip().upper()[:50]
    if not full_name:
        raise BusinessError("Le nom et prénoms est obligatoire.")
    if not EMAIL_RE.match(email):
        raise BusinessError("Adresse email invalide.")
    if not phone:
        raise BusinessError("Le numéro de téléphone est obligatoire.")
    check_new_password(form.get("password", ""), form.get("password_confirm", ""))

    if db.session.scalar(select(User.id).where(func.lower(User.email) == email)):
        raise BusinessError("Un compte existe déjà avec cet email : connectez-vous. "
                            "Si vous avez été convoqué par la DFS, utilisez le mot de passe qui vous a été communiqué.")
    # Same person already in the list (imported) under another email: don't create a second file.
    clauses = [func.lower(Candidate.email) == email, Candidate.phone == phone]
    if cni:
        clauses.append(Candidate.cni == cni)
    if db.session.scalar(select(Candidate.id).where(or_(*clauses)).limit(1)):
        raise BusinessError("Un dossier existe déjà avec cet email, ce téléphone ou cette CNI. "
                            "Contactez la Digital Farming School pour obtenir vos accès.")

    candidate = Candidate(
        full_name=full_name, email=email, phone=phone, cni=cni or None,
        birth_date=_parse_birth_date(form.get("birth_date", "")),
        gender=form.get("gender") if form.get("gender") in ("M", "F") else None,
        city=form.get("city", "").strip()[:120] or None,
    )
    account = _new_account(candidate, email, generate_password_hash(form["password"]), must_change=False)
    db.session.add_all([candidate, account])
    db.session.flush()
    audit_service.log("candidate_signup", candidate, {"email": email}, user=account)
    db.session.commit()
    return account


def _photo_type(data: bytes) -> str | None:
    """Real content type from the first bytes (never trust the file name). No SVG: it can carry scripts."""
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def update_profile(candidate: Candidate, form, photo=None) -> None:
    """The candidate edits contact details and photo. Name, CNI and email (login, file matching) stay with the DFS.
    On BusinessError the caller rolls back."""
    data = None
    if photo is not None and photo.filename and not form.get("remove_photo"):
        data = photo.read(PHOTO_MAX_BYTES + 1)
        if len(data) > PHOTO_MAX_BYTES:
            raise BusinessError("Photo trop lourde : 2 Mo maximum.")
        if _photo_type(data) is None:
            raise BusinessError("Photo invalide : formats JPEG, PNG ou WebP uniquement.")
    phone = norm_phone(form.get("phone"))
    if not phone:
        raise BusinessError("Le numéro de téléphone est obligatoire.")
    if db.session.scalar(select(Candidate.id).where(Candidate.phone == phone, Candidate.id != candidate.id).limit(1)):
        raise BusinessError("Ce numéro de téléphone est déjà utilisé par un autre dossier.")
    candidate.phone = phone
    candidate.birth_date = _parse_birth_date(form.get("birth_date", ""))
    candidate.gender = form.get("gender") if form.get("gender") in ("M", "F") else None
    candidate.city = form.get("city", "").strip()[:120] or None
    if form.get("remove_photo"):
        candidate.photo = candidate.photo_type = None
    elif data:
        candidate.photo, candidate.photo_type = data, _photo_type(data)
    audit_service.log("profile_update", candidate, {"photo": "removed" if form.get("remove_photo") else bool(data)})
    db.session.commit()


def photo_response(candidate: Candidate):
    if not candidate.photo_type:
        abort(404)
    response = send_file(io.BytesIO(candidate.photo), mimetype=candidate.photo_type, max_age=0)
    response.headers["Cache-Control"] = "private, no-cache"  # personal data: never in shared caches
    return response


def change_password(account: User, current: str, new: str, confirm: str) -> None:
    if not account.check_password(current):
        raise BusinessError("Mot de passe actuel incorrect.")
    check_new_password(new, confirm)
    account.set_password(new)
    account.must_change_password = False
    audit_service.log("password_change", account)
    db.session.commit()
