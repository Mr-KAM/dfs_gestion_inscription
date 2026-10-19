"""Matricule QR code: (random noise + matricule -> binary -> base64) + HMAC signature, decoded by the admin scanner."""
import base64

import pytest

from app.services.candidate_service import BusinessError, decode_matricule, encode_matricule
from tests.conftest import login, make_candidate


def _unsigned(text: str) -> str:
    bits = "".join(f"{b:08b}" for b in text.encode())
    return base64.urlsafe_b64encode(bits.encode()).decode().rstrip("=")


def test_round_trip_with_random_noise(app):
    a, b = encode_matricule("26CAND000001"), encode_matricule("26CAND000001")
    assert a != b  # 5 random chars on each side -> different payload and signature
    payload = a.split(".")[0]
    bits = base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)).decode()
    assert set(bits) == {"0", "1"} and len(bits) == 22 * 8
    assert decode_matricule(a) == decode_matricule(b) == "26CAND000001"


def test_forgeries_are_rejected(app):
    good = encode_matricule("26CAND000001")
    payload, signature = good.split(".")
    forged_payload = _unsigned("aaaaa26CAND000002bbbbb")
    for token in (payload,                                   # signature stripped (old unsigned format)
                  f"{forged_payload}.{signature}",           # other matricule, signature reused
                  f"{payload}.{signature[:-1]}A" if signature[-1] != "A" else f"{payload}.{signature[:-1]}B",
                  f"{forged_payload}.AAAAAAAAAAAAAAAAAAAAAA"):
        with pytest.raises(BusinessError):
            decode_matricule(token)
    with pytest.raises(BusinessError, match="signature invalide"):
        decode_matricule(f"{forged_payload}.{signature}")


def test_other_secret_key_is_rejected(app):
    token = encode_matricule("26CAND000001")
    app.config["SECRET_KEY"] = "another-server"
    with pytest.raises(BusinessError, match="signature invalide"):
        decode_matricule(token)


@pytest.mark.parametrize("token", ["", "hello", "26CAND000001", "x" * 1000, "é.é", ".", "abc."])
def test_garbage_is_rejected(app, token):
    with pytest.raises(BusinessError):
        decode_matricule(token)


def test_profiles_show_qr_and_admin_scan_decodes(app, client, admin):
    c = make_candidate()
    login(client, admin)
    assert "<svg" in client.get(f"/candidates/{c.id}").get_data(as_text=True)
    resp = client.get("/admin/scan")
    assert "camera=(self)" in resp.headers["Permissions-Policy"]
    html = client.post("/admin/scan", data={"token": encode_matricule(c.matricule)}).get_data(as_text=True)
    assert "Candidat identifié" in html and "Signature vérifiée" in html and c.matricule in html
    html = client.post("/admin/scan", data={"token": encode_matricule("26CAND999999")}).get_data(as_text=True)
    assert "aucun candidat" in html
    html = client.post("/admin/scan", data={"token": _unsigned(f"aaaaa{c.matricule}bbbbb") + ".x"}).get_data(as_text=True)
    assert "signature invalide" in html and "Candidat identifié" not in html
    assert client.get("/candidates/").headers["Permissions-Policy"].startswith("camera=()")  # camera only on scan


def test_scan_is_admin_only(app, client, motivation_user):
    login(client, motivation_user)
    assert client.get("/admin/scan").status_code == 403
