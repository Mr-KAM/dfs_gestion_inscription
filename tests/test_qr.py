"""Matricule QR code: (random noise + matricule as bytes) encrypted with AES-256-GCM, base32, decoded by the admin scanner."""
import base64

import pytest

from app.services.candidate_service import QR_NONCE, BusinessError, decode_matricule, encode_matricule
from tests.conftest import login, make_candidate


def _raw(token: str) -> bytes:
    return base64.b32decode(token + "=" * (-len(token) % 8))


def _token(raw: bytes) -> str:
    return base64.b32encode(raw).decode().rstrip("=")


def test_round_trip_and_content_is_encrypted(app):
    a, b = encode_matricule("26CAND000001"), encode_matricule("26CAND000001")
    assert a != b  # random noise + random nonce
    assert decode_matricule(a) == decode_matricule(b) == "26CAND000001"
    assert decode_matricule(a.lower()) == "26CAND000001"  # handheld scanner typing lowercase
    raw = _raw(a)
    assert len(a) == 80 and len(raw) == QR_NONCE + 22 + 16  # nonce + encrypted bytes + GCM tag
    assert b"26CAND" not in raw and b"26CAND" not in a.encode()  # nothing readable


def test_qr_is_small_enough_for_cameras(app):
    import segno
    qr = segno.make(encode_matricule("26CAND000001"), error="m")
    assert qr.mode == "alphanumeric" and qr.version <= 4  # was version 12: too dense to scan


def test_svg_scales_instead_of_being_cropped(app):
    """Fixed width/height without viewBox + CSS width = the browser crops the code (was unreadable)."""
    from app.services.candidate_service import matricule_qr
    svg = str(matricule_qr(make_candidate()))
    tag = svg[:svg.index(">")]
    assert "viewBox=" in tag and "width=" not in tag and "height=" not in tag


def test_tampering_is_rejected(app):
    raw = bytearray(_raw(encode_matricule("26CAND000001")))
    for index in (0, QR_NONCE, len(raw) // 2, len(raw) - 1):  # nonce, ciphertext, tag
        tampered = bytearray(raw)
        tampered[index] ^= 0x01
        with pytest.raises(BusinessError, match="signature invalide"):
            decode_matricule(_token(bytes(tampered)))
    with pytest.raises(BusinessError, match="signature invalide"):  # home-made "QR code"
        decode_matricule(_token(bytes(len(raw))))


def test_other_secret_key_is_rejected(app):
    token = encode_matricule("26CAND000001")
    app.config["SECRET_KEY"] = "another-server"
    with pytest.raises(BusinessError, match="signature invalide"):
        decode_matricule(token)


@pytest.mark.parametrize("token", ["", "hello", "26CAND000001", "x" * 1000, "é", "abc.def", "AAAA"])
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
    assert "Candidat identifié" in html and "contenu déchiffré" in html and c.matricule in html
    html = client.post("/admin/scan", data={"token": encode_matricule("26CAND999999")}).get_data(as_text=True)
    assert "aucun candidat" in html
    forged = _token(bytes(QR_NONCE + 22 + 16))
    html = client.post("/admin/scan", data={"token": forged}).get_data(as_text=True)
    assert "signature invalide" in html and "Candidat identifié" not in html
    assert client.get("/candidates/").headers["Permissions-Policy"].startswith("camera=()")  # camera only on scan


def test_scan_is_admin_only(app, client, motivation_user):
    login(client, motivation_user)
    assert client.get("/admin/scan").status_code == 403
