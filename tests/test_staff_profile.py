"""Staff profile (coach, reception, admin...): name, phone, photo, password."""
import io

from app.models import User
from tests.conftest import PASSWORD, login, make_user
from tests.test_portal import _logged_candidate

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


def _post(client, **form):
    photo = form.pop("photo", None)
    data = {"action": "profile", **form}
    if photo is not None:
        data["photo"] = (io.BytesIO(photo), "moi.png")
    return client.post("/profil", data=data, content_type="multipart/form-data", follow_redirects=True)


def test_coach_updates_profile_photo_and_password(app, db, client):
    coach = make_user("coach", "coach@test.ci")
    login(client, coach)
    assert "/profil" in client.get("/campus/").get_data(as_text=True)  # header avatar + menu link
    assert client.get("/profil/photo").status_code == 404
    html = _post(client, full_name="  Koffi   Yao ", phone="+225 07 07 07 07 07", photo=PNG,
                 email="pirate@x.ci", role="admin").get_data(as_text=True)
    assert "Profil mis à jour" in html and "/profil/photo?v=" in html
    db.session.expire_all()
    user = db.session.get(User, coach.id)
    assert (user.full_name, user.phone, user.photo_type) == ("Koffi Yao", "0707070707", "image/png")
    assert (user.email, user.role) == ("coach@test.ci", "coach")  # email / role: admin only
    resp = client.get("/profil/photo")
    assert resp.data == PNG and "private" in resp.headers["Cache-Control"]
    assert "JPEG, PNG ou WebP" in _post(client, photo=b"<svg/>").get_data(as_text=True)
    _post(client, full_name="Koffi Yao", remove_photo="1")
    db.session.expire_all()
    assert db.session.get(User, coach.id).photo_type is None

    html = client.post("/profil", data={"action": "password", "current_password": "mauvais",
                                        "password": "Nouveau123", "password_confirm": "Nouveau123"},
                       follow_redirects=True).get_data(as_text=True)
    assert "Mot de passe actuel incorrect" in html
    client.post("/profil", data={"action": "password", "current_password": PASSWORD,
                                 "password": "Nouveau123", "password_confirm": "Nouveau123"})
    db.session.expire_all()
    assert db.session.get(User, coach.id).check_password("Nouveau123")


def test_candidate_goes_to_his_own_profile(app, db, client):
    _logged_candidate(client)
    assert client.get("/profil").headers["Location"].endswith("/candidat/profil")
    assert client.get("/profil/photo").status_code == 404
