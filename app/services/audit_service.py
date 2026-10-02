from flask import has_request_context, request
from flask_login import current_user

from app.extensions import db
from app.models import AuditLog

ACTION_LABELS = {
    "login": "Connexion",
    "login_failed": "Échec de connexion",
    "logout": "Déconnexion",
    "user_create": "Création utilisateur",
    "user_update": "Modification utilisateur",
    "user_deactivate": "Désactivation utilisateur",
    "user_activate": "Activation utilisateur",
    "user_password_reset": "Réinitialisation mot de passe",
    "candidate_import": "Import candidats",
    "candidate_create": "Création candidat",
    "candidate_update": "Mise à jour candidat",
    "candidate_delete": "Suppression candidat",
    "motivation_validate": "Validation motivation",
    "exam_code_create": "Création code examen",
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
    "settings_update": "Modification paramètres",
    "export": "Export",
}


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
