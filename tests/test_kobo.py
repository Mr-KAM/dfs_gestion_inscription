import httpx
import pytest

from app.extensions import db
from app.models import Candidate, KoboSubmission
from app.services import kobo_service, settings_service
from tests.conftest import login, make_candidate

BASE = "https://kobo.test"
SUBMISSIONS = [
    {"_id": 1, "_submission_time": "2026-09-15T08:00:00", "identite/cni": "ci 001", "dossier": "Oui"},
    {"_id": 2, "_submission_time": "2026-09-15T08:05:00", "email": "SERGE@test.ci", "dossier": "Non"},
    {"_id": 3, "_submission_time": "2026-09-15T08:10:00", "nom": "Inconnu Total", "dossier": "Oui"},
    {"_id": 4, "_submission_time": "2026-09-15T08:15:00", "telephone": "+225 05 05 05 05 05", "dossier": "À vérifier"},
]


def transport(pages=None, status=200):
    """Fake Kobo API: two pages of data, plus the asset endpoint."""
    pages = pages or [SUBMISSIONS[:2], SUBMISSIONS[2:]]

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Token secret-token"
        if status != 200:
            return httpx.Response(status)
        if request.url.path.endswith("/data/"):
            page = int(request.url.params.get("page", "0"))
            nxt = f"{BASE}/api/v2/assets/aXYZ/data/?format=json&page={page + 1}" if page + 1 < len(pages) else None
            return httpx.Response(200, json={"count": len(SUBMISSIONS), "next": nxt, "results": pages[page]})
        return httpx.Response(200, json={"name": "Dépôt dossiers DFS"})
    return httpx.MockTransport(handler)


@pytest.fixture()
def configured(app):
    settings_service.set_many({
        "kobo_base_url": BASE, "kobo_asset_uid": "aXYZ", "kobo_token": "secret-token",
        "kobo_field_cni": "cni", "kobo_field_email": "email", "kobo_field_phone": "telephone",
        "kobo_field_name": "nom", "kobo_field_status": "dossier",
    })
    db.session.commit()


def test_token_encrypted_at_rest(configured):
    from app.models import Setting
    stored = db.session.get(Setting, "kobo_token").value
    assert stored != "secret-token" and "secret" not in stored
    assert settings_service.get("kobo_token") == "secret-token"


def test_connection_ok(configured):
    assert kobo_service.client_from_settings(transport()).test_connection() == "Dépôt dossiers DFS"


def test_kobo_errors(configured):
    with pytest.raises(kobo_service.KoboError, match="Token"):
        kobo_service.client_from_settings(transport(status=401)).test_connection()
    with pytest.raises(kobo_service.KoboError, match="HTTP 500"):
        kobo_service.sync(kobo_service.client_from_settings(transport(status=500)))

    def boom(request):
        raise httpx.ConnectError("down")
    with pytest.raises(kobo_service.KoboError, match="injoignable"):
        kobo_service.client_from_settings(httpx.MockTransport(boom)).test_connection()


def test_sync_matches_candidates(configured):
    aya = make_candidate(full_name="KOUAME Aya", email="aya@test.ci", phone="0707070707", cni="CI001")
    serge = make_candidate(full_name="YAO Serge", email="serge@test.ci", phone="0101010101", cni="CI002")
    never = make_candidate(full_name="BROU Koffi", email="k@test.ci", phone="0202020202", cni="CI003")

    result = kobo_service.sync(kobo_service.client_from_settings(transport()))
    assert result == {"total": 4, "matched": 2, "ambiguous": 0, "unmatched": 2}
    assert db.session.get(Candidate, aya.id).physical_file_status == "validated"   # by CNI, grouped field path
    assert db.session.get(Candidate, serge.id).physical_file_status == "missing"   # by email, case-insensitive
    assert db.session.get(Candidate, never.id).physical_file_status is None        # stays "Non synchronisé"
    assert db.session.get(Candidate, never.id).kobo_badge == ("secondary", "Non synchronisé")
    assert settings_service.get("kobo_last_sync")

    # Re-sync is idempotent (upsert on Kobo _id).
    kobo_service.sync(kobo_service.client_from_settings(transport()))
    assert db.session.query(KoboSubmission).count() == 4


def test_ambiguous_match_is_never_auto_assigned(configured):
    make_candidate(full_name="A", email="a@test.ci", phone="0505050505", cni="X1")
    make_candidate(full_name="B", email="b@test.ci", phone="0505050505", cni="X2")
    result = kobo_service.sync(kobo_service.client_from_settings(transport(pages=[[SUBMISSIONS[3]]])))
    assert result["ambiguous"] == 1
    sub = db.session.query(KoboSubmission).one()
    assert sub.match_status == "ambiguous" and sub.candidate_id is None
    assert all(c.physical_file_status is None for c in db.session.query(Candidate))


def test_manual_resolution_survives_resync(configured, client, admin):
    target = make_candidate(full_name="A", email="a@test.ci", phone="0505050505", cni="X1")
    make_candidate(full_name="B", email="b@test.ci", phone="0505050505", cni="X2")
    one_page = transport(pages=[[SUBMISSIONS[3]]])
    kobo_service.sync(kobo_service.client_from_settings(one_page))
    sub = db.session.query(KoboSubmission).one()

    login(client, admin)
    client.post(f"/admin/kobo/submissions/{sub.id}/resolve", data={"candidate": f"{target.id} — A"})
    kobo_service.sync(kobo_service.client_from_settings(one_page))
    sub = db.session.query(KoboSubmission).one()
    assert sub.match_status == "manual" and sub.candidate_id == target.id
    assert db.session.get(Candidate, target.id).physical_file_status == "pending"


def test_status_mapping():
    assert kobo_service.map_file_status("Oui") == "validated"
    assert kobo_service.map_file_status("Non trouvé") == "not_found"
    assert kobo_service.map_file_status("À vérifier") == "pending"
    assert kobo_service.map_file_status("??") == "pending"


def test_token_never_rendered(configured, client, admin):
    login(client, admin)
    for url in ("/admin/kobo/", "/admin/settings"):
        assert "secret-token" not in client.get(url).get_data(as_text=True)
