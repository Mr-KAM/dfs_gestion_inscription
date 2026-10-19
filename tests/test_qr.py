"""Matricule QR code: random noise + matricule -> binary -> base64, decoded by the admin scanner."""
import base64

import pytest

from app.services import candidate_service
from app.services.candidate_service import BusinessError, decode_matricule, encode_matricule
from tests.conftest import login, make_candidate


def test_round_trip_with_random_noise():
    a, b = encode_matricule("26CAND000001"), encode_matricule("26CAND000001")
    assert a != b  # 5 random chars on each side
    bits = base64.urlsafe_b64decode(a + "=" * (-len(a) % 4)).decode()
    assert set(bits) == {"0", "1"} and len(bits) == 22 * 8
    assert decode_matricule(a) == decode_matricule(b) == "26CAND000001"


@pytest.mark.parametrize("token", ["", "hello", "26CAND000001", "x" * 1000,
                                   base64.urlsafe_b64encode(b"0101").decode(),
                                   encode_matricule("26CAND00000X")])
def test_garbage_is_rejected(token):
    with pytest.raises(BusinessError):
        decode_matricule(token)


def test_profiles_show_qr_and_admin_scan_decodes(app, client, admin):
    c = make_candidate()
    login(client, admin)
    assert "<svg" in client.get(f"/candidates/{c.id}").get_data(as_text=True)
    resp = client.get("/admin/scan")
    assert "camera=(self)" in resp.headers["Permissions-Policy"]
    html = client.post("/admin/scan", data={"token": encode_matricule(c.matricule)}).get_data(as_text=True)
    assert "Candidat identifié" in html and c.full_name in html and c.matricule in html
    html = client.post("/admin/scan", data={"token": encode_matricule("26CAND999999")}).get_data(as_text=True)
    assert "aucun candidat" in html
    assert client.get("/candidates/").headers["Permissions-Policy"].startswith("camera=()")  # camera only on scan


def test_scan_is_admin_only(app, client, motivation_user):
    login(client, motivation_user)
    assert client.get("/admin/scan").status_code == 403
