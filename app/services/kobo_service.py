"""KoboToolbox API client and submission <-> candidate synchronisation.

`sync()` is a plain function so it can be triggered by the admin button, by
`flask sync-kobo`, or later by a cron/scheduler without changes.
"""
from collections import defaultdict
from datetime import datetime, timezone

import httpx
from sqlalchemy import select

from app.extensions import db
from app.models import Candidate, KoboSubmission
from app.models.candidate import FILE_MISSING, FILE_NOT_FOUND, FILE_PENDING, FILE_VALIDATED
from app.models.kobo import MATCH_AMBIGUOUS, MATCH_MANUAL, MATCH_MATCHED, MATCH_UNMATCHED
from app.services import audit_service, settings_service
from app.utils import norm_cni, norm_email, norm_key, norm_name, norm_phone, utcnow

STATUS_WORDS = {
    FILE_VALIDATED: {"oui", "yes", "conforme", "valide", "validated", "complet", "ok", "true", "1"},
    FILE_MISSING: {"non", "no", "nonconforme", "incomplet", "missing", "false", "0"},
    FILE_NOT_FOUND: {"nontrouve", "notfound", "absent", "introuvable"},
    FILE_PENDING: {"averifier", "pending", "enattente", "verifier"},
}


class KoboError(Exception):
    pass


class KoboClient:
    def __init__(self, base_url: str, token: str, asset_uid: str, transport: httpx.BaseTransport | None = None):
        if not (base_url and token and asset_uid):
            raise KoboError("Configuration Kobo incomplète (URL, Asset UID et token requis).")
        self.asset_uid = asset_uid.strip()
        self.http = httpx.Client(
            base_url=base_url.rstrip("/"), headers={"Authorization": f"Token {token.strip()}"},
            timeout=30, transport=transport, follow_redirects=True,
        )

    def _get(self, url: str, params: dict | None = None) -> dict:
        try:
            response = self.http.get(url, params=params)
        except httpx.HTTPError as exc:
            raise KoboError(f"Kobo injoignable : {exc.__class__.__name__}") from exc
        if response.status_code in (401, 403):
            raise KoboError("Token Kobo refusé (401/403).")
        if response.status_code == 404:
            raise KoboError("Formulaire Kobo introuvable : vérifiez l'Asset UID.")
        if response.status_code >= 400:
            raise KoboError(f"Erreur Kobo HTTP {response.status_code}.")
        try:
            return response.json()
        except ValueError as exc:
            raise KoboError("Réponse Kobo invalide (JSON attendu).") from exc

    def test_connection(self) -> str:
        return self._get(f"/api/v2/assets/{self.asset_uid}/", {"format": "json"}).get("name", self.asset_uid)

    def submissions(self) -> list[dict]:
        results, url = [], f"/api/v2/assets/{self.asset_uid}/data/"
        params = {"format": "json", "limit": 1000}
        while url:
            page = self._get(url, params)
            results.extend(page.get("results", []))
            url, params = page.get("next"), None  # `next` already carries the query string
        return results


def client_from_settings(transport=None) -> KoboClient:
    return KoboClient(settings_service.get("kobo_base_url"), settings_service.get("kobo_token"),
                      settings_service.get("kobo_asset_uid"), transport)


def get_field(data: dict, path: str):
    """Kobo keys are full paths ('group_x/cni'); accept the short name too."""
    if not path:
        return None
    if path in data:
        return data[path]
    return next((v for k, v in data.items() if k.split("/")[-1] == path), None)


def map_file_status(raw) -> str:
    key = norm_key(raw)
    for status, words in STATUS_WORDS.items():
        if key in words:
            return status
    return FILE_PENDING  # unknown answer -> human check


def _parse_time(value) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _build_matchers():
    """(label, settings key, normaliser, index) in the spec §14 priority order."""
    matchers = [
        ("CNI", "kobo_field_cni", norm_cni, "cni"),
        ("email", "kobo_field_email", norm_email, "email"),
        ("téléphone", "kobo_field_phone", norm_phone, "phone"),
        ("code candidat", "kobo_field_candidate", lambda v: str(v or "").strip().upper(), "candidate_number"),
        ("nom complet", "kobo_field_name", norm_name, "full_name"),
    ]
    # ponytail: in-memory indexes rebuilt per sync, fine for a few thousand candidates
    candidates = db.session.scalars(select(Candidate)).all()
    built = []
    for label, setting, normalise, attr in matchers:
        field = settings_service.get(setting)
        if not field:
            continue
        index = defaultdict(set)
        for c in candidates:
            key = normalise(getattr(c, attr))
            if key:
                index[key].add(c.id)
        built.append((label, field, normalise, index))
    return built


def match_candidate(data: dict, matchers) -> tuple[str, int | None, str | None]:
    """Return (match_status, candidate_id, field_label). Never auto-picks among several."""
    for label, field, normalise, index in matchers:
        key = normalise(get_field(data, field))
        if not key:
            continue
        ids = index.get(key, set())
        if len(ids) == 1:
            return MATCH_MATCHED, next(iter(ids)), label
        if len(ids) > 1:
            return MATCH_AMBIGUOUS, None, label
    return MATCH_UNMATCHED, None, None


def sync(client: KoboClient | None = None, user=None) -> dict:
    client = client or client_from_settings()
    records = client.submissions()
    matchers = _build_matchers()
    if not matchers:
        raise KoboError("Aucun champ de correspondance Kobo configuré.")
    status_field = settings_service.get("kobo_field_status")
    existing = {s.kobo_id: s for s in db.session.scalars(select(KoboSubmission))}
    stats = defaultdict(int, total=len(records))

    for data in records:
        kobo_id = str(data.get("_id") or data.get("_uuid") or "")
        if not kobo_id:
            continue
        submission = existing.get(kobo_id) or KoboSubmission(kobo_id=kobo_id)
        submission.data = data
        submission.submitted_at = _parse_time(data.get("_submission_time"))
        submission.file_status = map_file_status(get_field(data, status_field)) if status_field else FILE_VALIDATED
        if submission.match_status != MATCH_MANUAL:  # admin decisions survive re-syncs
            submission.match_status, submission.candidate_id, submission.match_field = match_candidate(data, matchers)
        stats[submission.match_status] += 1
        db.session.add(submission)
    db.session.flush()

    # Candidate file status = status of their most recent submission.
    latest = {}
    for s in db.session.scalars(
        select(KoboSubmission).where(KoboSubmission.candidate_id.is_not(None)).order_by(KoboSubmission.submitted_at)
    ):
        latest[s.candidate_id] = s.file_status
    for candidate in db.session.scalars(select(Candidate).where(Candidate.id.in_(list(latest)))):
        candidate.physical_file_status = latest[candidate.id]

    result = {"total": stats["total"], "matched": stats[MATCH_MATCHED] + stats[MATCH_MANUAL],
              "ambiguous": stats[MATCH_AMBIGUOUS], "unmatched": stats[MATCH_UNMATCHED]}
    settings_service.set_many({"kobo_last_sync": utcnow().isoformat()})
    audit_service.log("kobo_sync", details=result, user=user)
    db.session.commit()
    return result


def resolve(submission: KoboSubmission, candidate: Candidate | None) -> None:
    """Manual association decided by an admin (or un-association)."""
    submission.candidate_id = candidate.id if candidate else None
    submission.match_status = MATCH_MANUAL if candidate else MATCH_UNMATCHED
    submission.match_field = "manuel" if candidate else None
    if candidate:
        candidate.physical_file_status = submission.file_status
    audit_service.log("kobo_resolve", submission, {"candidate_id": candidate.id if candidate else None})
    db.session.commit()
