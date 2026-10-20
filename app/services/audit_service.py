from datetime import timedelta

from flask import has_request_context, request
from flask_login import current_user
from sqlalchemy import func, select

from app.extensions import db
from app.models import AuditLog
from app.utils import utcnow

ACTION_LABELS = {
    "login": "Connexion",
    "login_failed": "Échec de connexion",
    "login_blocked": "Connexion bloquée (trop d'essais)",
    "signup_rejected": "Inscription refusée",
    "logout": "Déconnexion",
    "user_create": "Création utilisateur",
    "user_update": "Modification utilisateur",
    "user_deactivate": "Désactivation utilisateur",
    "user_activate": "Activation utilisateur",
    "user_password_reset": "Réinitialisation mot de passe",
    "password_change": "Changement de mot de passe",
    "profile_update": "Mise à jour du profil (candidat)",
    "candidate_accounts_create": "Création comptes candidats",
    "candidate_signup": "Inscription candidat",
    "candidate_import": "Import candidats",
    "results_import": "Import résultats des tests",
    "candidate_create": "Création candidat",
    "candidate_update": "Mise à jour candidat",
    "candidate_delete": "Suppression candidat",
    "motivation_validate": "Validation motivation",
    "exam_code_create": "Création code examen",
    "motivation_passed": "Validation test motivation (case)",
    "technical_passed": "Validation test technique (case)",
    "workstation_assign": "Attribution ordinateur",
    "test_start": "Démarrage test",
    "test_pause": "Pause",
    "test_resume": "Reprise",
    "test_stop": "Arrêt",
    "test_complete": "Fin de test",
    "kobo_sync": "Synchronisation Kobo",
    "kobo_config": "Configuration Kobo",
    "kobo_resolve": "Correspondance Kobo manuelle",
    "room_update": "Gestion salles",
    "sas_update": "Gestion SAS",
    "sas_admit": "Admission au SAS",
    "sas_remove": "Retrait du SAS",
    "sas_assign": "Attribution classe / chambre / matériel SAS",
    "settings_update": "Modification paramètres",
    "export": "Export",
}


def recent_count(action: str, minutes: int, ip: str | None = None, email: str | None = None) -> int:
    """How many `action` rows in the last `minutes` (rate limiting, shared by all gunicorn workers)."""
    stmt = select(func.count(AuditLog.id)).where(
        AuditLog.action == action, AuditLog.created_at >= utcnow() - timedelta(minutes=minutes))
    if ip is not None:
        stmt = stmt.where(AuditLog.ip_address == ip)
    if email is not None:
        stmt = stmt.where(AuditLog.details["email"].as_string() == email)
    return db.session.scalar(stmt) or 0


def log(action: str, entity=None, details: dict | None = None, user=None) -> None:
    """Stage an audit row in the current transaction (the caller commits)."""
    if user is None and has_request_context() and current_user.is_authenticated:
        user = current_user
    db.session.add(AuditLog(
        user_id=getattr(user, "id", None),
        action=action,
        entity_type=type(entity).__name__ if entity is not None else None,
        entity_id=str(entity.id) if getattr(entity, "id", None) is not None else None,
        details=details or None,
        ip_address=request.remote_addr if has_request_context() else None,
    ))
