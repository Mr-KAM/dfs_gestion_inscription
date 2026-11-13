"""The app's two domains: recruitment (DFS) and Zeus. Which one a request came in on, absolute URLs on either one,
and outgoing email with each domain's own SMTP. Base URLs and SMTP: .env defaults, editable in Paramètres."""
import smtplib
import ssl
from email.message import EmailMessage
from urllib.parse import urlsplit

from flask import has_request_context, request, url_for

from app.services import settings_service
from app.services.candidate_service import BusinessError

RECRUITMENT, ZEUS = settings_service.SITES
SITE_LABELS = {RECRUITMENT: "Recrutement DFS", ZEUS: "Zeus"}
SMTP_SECURITIES = {"starttls": "STARTTLS (587)", "ssl": "SSL/TLS (465)", "none": "Aucune (déconseillé)"}


def base_url(site: str) -> str:
    """'https://zeus.example.ci', or '' when not configured."""
    return settings_service.get(f"{site}_base_url").rstrip("/")


def _host(url: str) -> str:
    return (urlsplit(url).hostname or "").lower()


def current_site() -> str:
    """ZEUS when the request came in on the Zeus domain (behind the proxy: ProxyFix gives the real host),
    RECRUITMENT otherwise (its domain, localhost, an IP...)."""
    zeus_host = _host(base_url(ZEUS))
    if has_request_context() and zeus_host and request.host.rsplit(":", 1)[0].lower() == zeus_host:
        return ZEUS
    return RECRUITMENT


def external_url(site: str, endpoint: str, **values) -> str:
    """Absolute URL of a page on the given domain, whatever the domain of the current request: links shared
    publicly or sent by email. Falls back to the current domain when that site has no base URL."""
    base = base_url(site)
    return base + url_for(endpoint, **values) if base else url_for(endpoint, _external=True, **values)


def smtp(site: str) -> dict:
    return {f: settings_service.get(f"smtp_{site}_{f}") for f in settings_service.SMTP_FIELDS}


def send_mail(to: str, subject: str, body: str, site: str | None = None) -> None:
    """Plain-text email through the SMTP of `site` (default: the domain of the current request)."""
    site = site or current_site()
    cfg = smtp(site)
    if not cfg["host"] or not cfg["sender"]:
        raise BusinessError(f"SMTP {SITE_LABELS[site]} non configuré (serveur et expéditeur requis).")
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = cfg["sender"], to, subject
    msg.set_content(body)
    security = cfg["security"] if cfg["security"] in SMTP_SECURITIES else "starttls"
    port = int(cfg["port"]) if cfg["port"].isdigit() else (465 if security == "ssl" else 587)
    try:
        if security == "ssl":
            server = smtplib.SMTP_SSL(cfg["host"], port, timeout=20, context=ssl.create_default_context())
        else:
            server = smtplib.SMTP(cfg["host"], port, timeout=20)
            if security == "starttls":
                server.starttls(context=ssl.create_default_context())
        with server:
            if cfg["user"]:
                server.login(cfg["user"], cfg["password"])
            server.send_message(msg)
    except (smtplib.SMTPException, OSError) as exc:
        raise BusinessError(f"Envoi impossible via {cfg['host']}:{port} ({exc.__class__.__name__}: {exc}).") from None
