"""Infrastructure and inventory (responsable de cité): buildings > levels > spaces, equipment, consumables."""
import io

import pandas as pd

from app.models import AuditLog, Building, Consumable, Equipment, Level, Space
from tests.conftest import login, make_user


def _tree(client):
    client.post("/infrastructures/buildings", data={"name": "Bâtiment A", "description": "Principal"})
    b = Building.query.one()
    client.post(f"/infrastructures/buildings/{b.id}/levels", data={"number": "0"})
    client.post(f"/infrastructures/buildings/{b.id}/levels", data={"number": "1", "name": "1er étage"})
    rdc, first = sorted(b.levels, key=lambda lv: lv.number)
    client.post(f"/infrastructures/levels/{rdc.id}/spaces", data={"name": "Salle 1", "usage": "Salle de classe",
                                                                    "capacity": "30"})
    client.post(f"/infrastructures/levels/{first.id}/spaces", data={"name": "CH-01", "usage": "Chambre", "capacity": "2"})
    return b, Space.query.filter_by(name="Salle 1").one(), Space.query.filter_by(name="CH-01").one()


def test_buildings_levels_spaces(app, db, client):
    login(client, make_user("residence", "cite@test.ci"))
    assert client.get("/").headers["Location"].endswith("/infrastructures/")
    b, classroom, bedroom = _tree(client)
    assert [lv.label for lv in b.levels] == ["Niveau 0 — Rez-de-chaussée", "Niveau 1 — 1er étage"]
    assert (classroom.usage, classroom.capacity, bedroom.full_label) == ("Salle de classe", 30, "Bâtiment A › N1 › CH-01")
    html = client.get("/infrastructures/").get_data(as_text=True)
    assert "Salle 1" in html and "Chambre" in html and "Rez-de-chaussée" in html
    html = client.post(f"/infrastructures/buildings/{b.id}/levels", data={"number": "1"},
                       follow_redirects=True).get_data(as_text=True)
    assert "existe déjà" in html
    html = client.post(f"/infrastructures/buildings/{b.id}/delete", follow_redirects=True).get_data(as_text=True)
    assert "Supprimez d" in html and Building.query.count() == 1  # not empty: kept
    client.post(f"/infrastructures/spaces/{bedroom.id}", data={"name": "CH-01", "usage": "Bureau", "capacity": ""})
    db.session.expire_all()
    assert bedroom.usage == "Bureau" and bedroom.capacity is None


def test_equipment_inventory_move_and_assign(app, db, client):
    residence = make_user("residence", "cite@test.ci")
    coach = make_user("coach", "coach@test.ci")
    login(client, residence)
    b, classroom, bedroom = _tree(client)
    client.post("/infrastructures/equipment", data={"name": "Vidéoprojecteur", "category": "Audiovisuel",
                                                    "brand": "Epson", "status": "in_service", "space_id": classroom.id})
    item = Equipment.query.one()
    assert item.code == f"INV-{item.id:05d}" and item.space_id == classroom.id
    html = client.post("/infrastructures/equipment", data={"name": "Autre", "code": item.code.lower()},
                       follow_redirects=True).get_data(as_text=True)
    assert "déjà utilisé" in html and Equipment.query.count() == 1

    form = {"name": "Vidéoprojecteur", "code": item.code, "category": "Audiovisuel", "status": "repair",
            "space_id": bedroom.id, "assigned_to": coach.id}
    client.post(f"/infrastructures/equipment/{item.id}", data=form)  # moved, lent to the coach, sent to repair
    db.session.expire_all()
    assert (item.space_id, item.assigned_to, item.status) == (bedroom.id, coach.id, "repair")
    log = AuditLog.query.filter_by(action="equipment_update").one()
    assert log.details["space"] == "Bâtiment A › N0 › Salle 1 → Bâtiment A › N1 › CH-01"
    assert log.details["holder"].endswith("→ coach") and "En réparation" in log.details["status"]
    html = client.get(f"/infrastructures/equipment/{item.id}").get_data(as_text=True)
    assert "Emplacement : Bâtiment A" in html and "Attribution" in html

    assert "Vidéoprojecteur" in client.get(f"/infrastructures/spaces/{bedroom.id}").get_data(as_text=True)
    assert "Vidéoprojecteur" not in client.get(f"/infrastructures/spaces/{classroom.id}").get_data(as_text=True)
    assert "Vidéoprojecteur" in client.get(f"/infrastructures/equipment?holder={coach.id}").get_data(as_text=True)
    assert "Aucun matériel" in client.get("/infrastructures/equipment?status=broken").get_data(as_text=True)
    html = client.post(f"/infrastructures/spaces/{bedroom.id}/delete", follow_redirects=True).get_data(as_text=True)
    assert "déplacez-les" in html and Space.query.count() == 2

    data = client.get(f"/infrastructures/equipment/export?format=xlsx&space={bedroom.id}").data
    sheet = pd.read_excel(io.BytesIO(data))
    assert list(sheet["N° inventaire"]) == [item.code] and sheet.loc[0, "Attribué à"] == "coach"


def test_consumables_stock(app, db, client):
    login(client, make_user("residence", "cite@test.ci"))
    client.post("/infrastructures/consumables", data={"name": "Eau 1,5 L", "category": "Eau", "unit": "pack",
                                                      "quantity": "10", "min_quantity": "3"})
    water = Consumable.query.one()
    assert water.quantity == 10 and [m.note for m in water.movements] == ["Stock initial"]
    client.post(f"/infrastructures/consumables/{water.id}/move", data={"quantity": "8", "direction": "out",
                                                                       "note": "Salle 1"})
    db.session.expire_all()
    assert water.quantity == 2 and water.is_low
    assert "Stock bas" in client.get("/infrastructures/").get_data(as_text=True)
    html = client.post(f"/infrastructures/consumables/{water.id}/move", data={"quantity": "5", "direction": "out"},
                       follow_redirects=True).get_data(as_text=True)
    assert "Stock insuffisant" in html
    client.post(f"/infrastructures/consumables/{water.id}/move", data={"quantity": "12", "direction": "in",
                                                                       "note": "Livraison"})
    db.session.expire_all()
    assert water.quantity == 14 and [m.delta for m in water.movements] == [12, -8, 10]
    assert client.post(f"/infrastructures/consumables/{water.id}/move",
                       data={"quantity": "1", "back": "https://evil.example/"}).headers["Location"].startswith("/")


def test_infrastructure_is_for_residence_and_admin(app, db, client):
    login(client, make_user("coach", "coach@test.ci"))
    for url in ("/infrastructures/", "/infrastructures/equipment", "/infrastructures/consumables"):
        assert client.get(url).status_code == 403, url
    assert client.post("/infrastructures/buildings", data={"name": "X"}).status_code == 403
    assert Level.query.count() == 0
