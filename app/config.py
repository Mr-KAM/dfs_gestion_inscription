import os

from sqlalchemy.engine import URL


def _bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if url:
        # Dokploy / Heroku style URLs -> psycopg 3 driver
        if url.startswith("postgres://"):
            url = "postgresql://" + url[len("postgres://"):]
        if url.startswith("postgresql://"):
            url = "postgresql+psycopg://" + url[len("postgresql://"):]
        return url
    if os.environ.get("POSTGRES_USER"):
        # URL.create escapes special characters in the password.
        return URL.create(
            "postgresql+psycopg",
            username=os.environ["POSTGRES_USER"],
            password=os.environ.get("POSTGRES_PASSWORD"),
            host=os.environ.get("POSTGRES_HOST", "db"),
            port=int(os.environ.get("POSTGRES_PORT", "5432")),
            database=os.environ.get("POSTGRES_DB", os.environ["POSTGRES_USER"]),
        ).render_as_string(hide_password=False)
    return ""


class Config:
    ENV = os.environ.get("FLASK_ENV", "production")
    IS_PRODUCTION = ENV == "production"

    SECRET_KEY = os.environ.get("SECRET_KEY", "")
    SQLALCHEMY_DATABASE_URI = _database_url()
    SQLALCHEMY_ENGINE_OPTIONS = {"pool_pre_ping": True}

    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = _bool("SESSION_COOKIE_SECURE", IS_PRODUCTION)
    REMEMBER_COOKIE_HTTPONLY = True
    REMEMBER_COOKIE_SECURE = SESSION_COOKIE_SECURE
    PERMANENT_SESSION_LIFETIME = 12 * 3600  # one recruitment day

    WTF_CSRF_TIME_LIMIT = None  # tied to the session instead of 1h
    MAX_CONTENT_LENGTH = int(os.environ.get("MAX_UPLOAD_MB", "10")) * 1024 * 1024
    UPLOAD_FOLDER = os.environ.get("UPLOAD_FOLDER", "/tmp/dfs-uploads")
    ALLOWED_IMPORT_EXTENSIONS = {"csv", "xlsx", "xls"}

    ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "")
    ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")

    DEFAULT_TECHNICAL_TEST_DURATION = int(os.environ.get("DEFAULT_TECHNICAL_TEST_DURATION", "60"))
    APP_NAME = os.environ.get("APP_NAME", "DFS Recruitment & Testing")
    TIMEZONE = os.environ.get("TIMEZONE", "Africa/Abidjan")
    EXAM_CODE_PREFIX = os.environ.get("EXAM_CODE_PREFIX", "DFS")

    KOBO_BASE_URL = os.environ.get("KOBO_BASE_URL", "https://kf.kobotoolbox.org")
    KOBO_TOKEN = os.environ.get("KOBO_TOKEN", "")
    KOBO_ASSET_UID = os.environ.get("KOBO_ASSET_UID", "")

    PER_PAGE = 25
