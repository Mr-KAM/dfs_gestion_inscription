"""Candidate space: the candidate updates contact details and a profile photo (stored in the database)."""
import io

from app.models import Candidate
from tests.conftest import login, make_candidate
from tests.test_portal import _logged_candidate

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


def _post(client, photo: bytes | None = None, name="moi.png", **form):
    data = {"phone": "0101010101", "city": "Bouaké", "birth_date": "2001-02-03", "gender": "F", **form}
    if photo is not None:
        data["photo"] = (io.BytesIO(photo), name)
    return client.post("/candidat/profil", data=data, content_type="multipart/form-data", follow_redirects=True)


def test_candidate_updates_profile_and_photo(app, db, client):
    c = _logged_candidate(client)
    assert "Modifier mes informations" in client.get("/candidat/").get_data(as_text=True)
    assert client.get("/candidat/photo").status_code == 404  # no photo yet
    html = _post(client, PNG, full_name="PIRATE", cni="X", email="x@x.ci").get_data(as_text=True)
    assert "Profil mis à jour" in html and "/candidat/photo?v=" in html
    db.session.expire_all()
    c = db.session.get(Candidate, c.id)
    assert (c.phone, c.city, c.birth_date.isoformat(), c.gender) == ("0101010101", "Bouaké", "2001-02-03", "F")
    assert c.full_name == "KOUAME Aya" and c.cni == "CI001" and c.email == "aya@test.ci"  # identity: DFS only
    resp = client.get("/candidat/photo")
    assert resp.data == PNG and resp.mimetype == "image/png" and "private" in resp.headers["Cache-Control"]
    _post(client, remove_photo="1")
    db.session.expire_all()
    assert db.session.get(Candidate, c.id).photo_type is None


def test_photo_is_checked(app, db, client):
    c = _logged_candidate(client)
    assert "JPEG, PNG ou WebP" in _post(client, b"<svg onload=alert(1)>", name="x.png").get_data(as_text=True)
    assert "2 Mo maximum" in _post(client, PNG + b"\x00" * (2 * 1024 * 1024)).get_data(as_text=True)
    make_candidate(email="b@test.ci", cni="B", phone="0505050505")
    assert "déjà utilisé" in _post(client, phone="05 05 05 05 05").get_data(as_text=True)
    db.session.expire_all()
    c = db.session.get(Candidate, c.id)
    assert c.photo_type is None and c.phone == "0707070707"  # nothing saved on error


def test_staff_sees_photo(app, db, client, admin):
    c = make_candidate(photo=PNG, photo_type="image/png")
    login(client, admin)
    assert f"/candidates/{c.id}/photo" in client.get(f"/candidates/{c.id}").get_data(as_text=True)
    assert client.get(f"/candidates/{c.id}/photo").data == PNG
