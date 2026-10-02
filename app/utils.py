"""Small shared helpers: time, normalisation, RBAC decorator, secret encryption."""
import base64
import hashlib
import re
import unicodedata
from datetime import datetime, timezone
from functools import wraps

from flask import abort, current_app
from flask_login import current_user, login_required


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(value: datetime | None) -> datetime | None:
    """SQLite returns naive datetimes; PostgreSQL returns aware ones."""
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def strip_accents(value: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", value) if not unicodedata.combining(c))


def norm_key(value) -> str:
    """'Nom et prénoms' -> 'nometprenoms' (header matching)."""
    return re.sub(r"[^a-z0-9]", "", strip_accents(str(value or "")).lower())


def norm_name(value) -> str:
    return " ".join(strip_accents(str(value or "")).upper().split())


def norm_email(value) -> str:
    return str(value or "").strip().lower()


def norm_phone(value) -> str:
    """Keep the last 10 digits (Côte d'Ivoire numbering, drops +225/00225)."""
    digits = re.sub(r"\D", "", str(value or ""))
    if digits.startswith("225") and len(digits) == 13:
        digits = digits[3:]
    if len(digits) == 9:  # ponytail: Excel dropped the leading 0
        digits = "0" + digits
    return digits[-10:] if len(digits) > 10 else digits


def norm_cni(value) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(value or "").upper())


def roles_required(*roles: str):
    """Server-side RBAC: login required + role membership, else 403."""
    def decorator(view):
        @wraps(view)
        @login_required
        def wrapped(*args, **kwargs):
            if current_user.role not in roles:
                abort(403)
            return view(*args, **kwargs)
        return wrapped
    return decorator


def _fernet():
    from cryptography.fernet import Fernet

    key = hashlib.sha256(current_app.config["SECRET_KEY"].encode()).digest()
    return Fernet(base64.urlsafe_b64encode(key))


def encrypt_secret(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode()


def decrypt_secret(value: str) -> str:
    """Returns '' if SECRET_KEY changed since the secret was stored."""
    from cryptography.fernet import InvalidToken

    try:
        return _fernet().decrypt(value.encode()).decode()
    except (InvalidToken, ValueError):
        current_app.logger.warning("Impossible de déchiffrer un secret stocké (SECRET_KEY modifiée ?)")
        return ""
