import logging
import os
from datetime import date, datetime
from zoneinfo import ZoneInfo

from flask import Flask, jsonify, render_template, request
from flask_wtf.csrf import CSRFError
from sqlalchemy import text
from werkzeug.middleware.proxy_fix import ProxyFix

from app.config import Config
from app.extensions import csrf, db, login_manager, migrate


def create_app(config_overrides: dict | None = None) -> Flask:
    app = Flask(__name__)
    app.config.from_object(Config)
    app.config.update(config_overrides or {})
    _check_config(app)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    # Dokploy/Traefik terminate TLS: trust one proxy hop for scheme + client IP.
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
    os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)

    db.init_app(app)
    migrate.init_app(app, db, compare_type=True)
    login_manager.init_app(app)
    csrf.init_app(app)

    from app import models  # noqa: F401  (register tables for Alembic)
    from app.admin import bp as admin_bp
    from app.auth import bp as auth_bp
    from app.candidates import bp as candidates_bp
    from app.cli import register_cli
    from app.dashboard import bp as dashboard_bp
    from app.kobo import bp as kobo_bp
    from app.motivation import bp as motivation_bp
    from app.technical import bp as technical_bp

    for bp in (auth_bp, dashboard_bp, candidates_bp, motivation_bp, technical_bp, kobo_bp, admin_bp):
        app.register_blueprint(bp)
    register_cli(app)
    _register_web_helpers(app)
    return app


def _check_config(app: Flask) -> None:
    if not app.config["SQLALCHEMY_DATABASE_URI"]:
        raise RuntimeError("DATABASE_URL (ou POSTGRES_USER/POSTGRES_PASSWORD/POSTGRES_DB) doit être défini.")
    if not app.config["SECRET_KEY"]:
        if app.config["IS_PRODUCTION"]:
            raise RuntimeError("SECRET_KEY doit être défini en production.")
        app.config["SECRET_KEY"] = "dev-insecure-key"


def _register_web_helpers(app: Flask) -> None:
    from app.models.user import ROLES
    from app.services import settings_service

    @app.get("/health")
    def health():
        try:
            db.session.execute(text("SELECT 1"))
            return jsonify(status="ok", database="connected")
        except Exception:  # noqa: BLE001 - health must never raise
            app.logger.exception("Healthcheck database failure")
            return jsonify(status="error", database="unreachable"), 503

    @app.template_filter("dt")
    def format_datetime(value, fmt="%d/%m/%Y %H:%M"):
        if not value:
            return ""
        if isinstance(value, str):
            value = datetime.fromisoformat(value)
        tz = ZoneInfo(settings_service.get("timezone") or "UTC")
        if value.tzinfo is None:
            value = value.replace(tzinfo=ZoneInfo("UTC"))
        return value.astimezone(tz).strftime(fmt)

    @app.template_filter("d")
    def format_date(value):
        return value.strftime("%d/%m/%Y") if isinstance(value, (date, datetime)) else (value or "")

    @app.template_filter("hms")
    def format_seconds(seconds):
        seconds = max(0, int(seconds or 0))
        return f"{seconds // 3600:02d}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"

    @app.context_processor
    def inject_globals():
        return {"app_name": settings_service.get("app_name"), "ROLES": ROLES}

    @app.after_request
    def security_headers(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        return response

    def error_page(code: int, title: str, message: str):
        if request.path.startswith("/api/") or request.accept_mimetypes.best == "application/json":
            return jsonify(error=title), code
        return render_template("error.html", code=code, title=title, message=message), code

    app.register_error_handler(403, lambda e: error_page(403, "Accès refusé", "Votre rôle ne permet pas d'accéder à cette page."))
    app.register_error_handler(404, lambda e: error_page(404, "Page introuvable", "La ressource demandée n'existe pas."))
    app.register_error_handler(413, lambda e: error_page(413, "Fichier trop volumineux", "Le fichier dépasse la taille autorisée."))
    app.register_error_handler(CSRFError, lambda e: error_page(400, "Session expirée", "Le formulaire a expiré, rechargez la page et réessayez."))

    @app.errorhandler(500)
    def internal_error(e):
        db.session.rollback()
        return error_page(500, "Erreur interne", "Une erreur inattendue est survenue. Elle a été journalisée.")
