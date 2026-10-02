"""Admin-editable settings stored in the `settings` table, with env defaults."""
from datetime import date

from flask import current_app, g, has_request_context

from app.extensions import db
from app.models import Setting
from app.utils import decrypt_secret, encrypt_secret

SECRET_KEYS = {"kobo_token"}
KOBO_FIELDS = {
    "kobo_field_cni": "Champ CNI",
    "kobo_field_email": "Champ email",
    "kobo_field_phone": "Champ téléphone",
    "kobo_field_candidate": "Champ candidat (N° / code)",
    "kobo_field_name": "Champ nom complet",
    "kobo_field_status": "Champ statut dossier",
}


def _defaults() -> dict[str, str]:
    cfg = current_app.config
    return {
        "app_name": cfg["APP_NAME"],
        "timezone": cfg["TIMEZONE"],
        "campaign_name": "Recrutement DFS",
        "campaign_year": str(date.today().year),
        "exam_code_prefix": cfg["EXAM_CODE_PREFIX"],
        "exam_code_format": "{prefix}-{year}-{seq:04d}",
        "technical_duration": str(cfg["DEFAULT_TECHNICAL_TEST_DURATION"]),
        "kobo_base_url": cfg["KOBO_BASE_URL"],
        "kobo_asset_uid": cfg["KOBO_ASSET_UID"],
        "kobo_last_sync": "",
        **{key: "" for key in KOBO_FIELDS},
    }


def _all() -> dict[str, str]:
    if has_request_context() and "settings" in g:
        return g.settings
    values = _defaults()
    values.update({s.key: s.value for s in db.session.scalars(db.select(Setting))})
    if has_request_context():
        g.settings = values
    return values


def get(key: str) -> str:
    if key == "kobo_token":
        return get_secret(key) or current_app.config["KOBO_TOKEN"]
    return _all().get(key, "")


def get_int(key: str, default: int) -> int:
    try:
        return int(get(key))
    except (TypeError, ValueError):
        return default


def get_secret(key: str) -> str:
    stored = _all().get(key)
    return decrypt_secret(stored) if stored else ""


def set_many(values: dict[str, str]) -> None:
    """Stage values (caller commits). Secrets are encrypted at rest."""
    for key, value in values.items():
        value = (value or "").strip()
        if key in SECRET_KEYS:
            value = encrypt_secret(value) if value else ""
        setting = db.session.get(Setting, key) or Setting(key=key)
        setting.value = value
        db.session.add(setting)
    g.pop("settings", None)
