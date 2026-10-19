"""Admin > Salles & ordinateurs: import rooms and workstations from CSV / Excel, downloadable template."""
import io

import pandas as pd

from app.models import Room
from tests.conftest import login

CSV = ("salle;description;ordi;libelle\n"
       "Salle A;Bâtiment principal;PC-01;Poste formateur\n"
       "salle a;;PC-02;\n"          # same room, other case
       "Salle B;Annexe;;\n"          # room only
       ";;PC-09;\n")                 # no room -> error


def _upload(client, data: bytes, name: str):
    return client.post("/admin/rooms/import", data={"file": (io.BytesIO(data), name)},
                       content_type="multipart/form-data")


def _rooms(db):
    db.session.expire_all()
    return {r.name: (r.description, {w.computer_number: w.label for w in r.workstations}) for r in db.session.query(Room)}


def test_template_download(app, client, admin):
    login(client, admin)
    body = client.get("/admin/rooms/template").get_data(as_text=True)
    assert body.lstrip("﻿").startswith("salle;description;ordi;libelle") and "Salle A" in body


def test_csv_import_is_additive_and_idempotent(app, db, client, admin, room):  # fixture: Salle A, PC-01..04
    login(client, admin)
    html = _upload(client, CSV.encode("utf-8-sig"), "salles.csv").get_data(as_text=True)
    assert "1 salle(s) et 0 ordinateur(s) créés" in html and "1 ligne(s) en erreur" in html and "salle obligatoire" in html
    rooms = _rooms(db)
    assert rooms["Salle A"][0] == "Bâtiment principal" and rooms["Salle A"][1]["PC-01"] == "Poste formateur"
    assert len(rooms["Salle A"][1]) == 4 and rooms["Salle B"] == ("Annexe", {})  # existing PCs kept, none deleted
    html = _upload(client, CSV.encode(), "salles.csv").get_data(as_text=True)
    assert "0 salle(s) et 0 ordinateur(s) créés, 0 mise(s) à jour" in html  # same file again: no change
    assert _rooms(db) == rooms


def test_excel_import(app, db, client, admin):
    buffer = io.BytesIO()
    pd.DataFrame({"Salle": ["Labo 1", "Labo 1"], "Ordi": ["PC-01", "PC-02"], "Libellé": ["", "Fond"]}).to_excel(
        buffer, index=False)
    login(client, admin)
    _upload(client, buffer.getvalue(), "salles.xlsx")
    assert _rooms(db) == {"Labo 1": ("", {"PC-01": "", "PC-02": "Fond"})}


def test_missing_room_column(app, db, client, admin):
    login(client, admin)
    assert _upload(client, b"nom;ordi\nX;PC\n", "x.csv").status_code == 302
    assert _rooms(db) == {}


def test_rooms_import_is_admin_only(app, db, client, motivation_user):
    login(client, motivation_user)
    assert _upload(client, CSV.encode(), "salles.csv").status_code == 403
    assert client.get("/admin/rooms/template").status_code == 403
    assert _rooms(db) == {}
