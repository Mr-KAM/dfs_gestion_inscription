"""SAS (next recruitment stage): admin creates a SAS, admits candidates, assigns bedroom + equipment; candidates see it."""
import io

from app.models import Sas
from tests.conftest import login
from tests.test_portal import _logged_candidate, make_candidate


def _sas(client, name="SAS 2026"):
    client.post("/admin/sas/save", data={"name": name, "description": "Campus DFS"})
    return Sas.query.filter_by(name=name).one()


def test_admin_creates_sas_admits_and_assigns(app, db, client, admin):
    a = make_candidate(email="a@test.ci", motivation_passed=True, technical_passed=True,
                       motivation_completed=True, technical_completed=True, exam_code="E1")
    b = make_candidate(email="b@test.ci", full_name="B")
    make_candidate(email="c@test.ci", full_name="C")  # not admitted
    login(client, admin)
    sas = _sas(client)
    assert "1 candidat(s) ayant validé" in client.get(f"/admin/sas/{sas.id}").get_data(as_text=True)

    client.post(f"/admin/sas/{sas.id}/members", data={"validated": "1"})  # both tests validated -> a
    resp = client.post(f"/admin/sas/{sas.id}/members", data={"candidates": f"{b.matricule.lower()}, a@test.ci\nINCONNU"},
                       follow_redirects=True)
    html = resp.get_data(as_text=True)
    assert "1 candidat(s) admis" in html and "INCONNU" in html  # a already in: not counted twice
    db.session.expire_all()
    assert {c.email for c in sas.members} == {"a@test.ci", "b@test.ci"}
    assert b.exam_code and b.motivation_completed and b.technical_completed  # admission completes + validates
    assert b.motivation_passed and b.technical_passed and a.exam_code == "E1"  # existing code kept

    html = client.get(f"/admin/sas/{sas.id}").get_data(as_text=True)
    assert f"/candidates/{a.id}/passed/technical" in html  # validation ticked from the SAS page
    back = f"/admin/sas/{sas.id}?"
    resp = client.post(f"/candidates/{a.id}/passed/technical", data={"next": back})  # unticked: saved, back on the SAS
    db.session.expire_all()
    assert resp.headers["Location"].endswith(f"/admin/sas/{sas.id}") and not a.technical_passed

    client.post(f"/admin/sas/{sas.id}/members/{b.id}", data={"class": "Classe A", "bedroom": "B-12", "equipment": "Laptop n°12"})
    db.session.expire_all()
    assert (b.sas_class, b.sas_bedroom, b.sas_equipment) == ("Classe A", "B-12", "Laptop n°12")

    assert client.post(f"/admin/sas/{sas.id}/delete").status_code == 302 and db.session.get(Sas, sas.id)  # not empty
    client.post(f"/admin/sas/{sas.id}/members/{b.id}", data={"remove": "1"})
    db.session.expire_all()
    assert b.sas_id is None and b.sas_bedroom == b.sas_class == ""
    assert client.post(f"/admin/sas/{sas.id}/members/{b.id}", data={}).status_code == 404  # no longer a member


def test_candidate_sees_admission(app, db, client):
    c = _logged_candidate(client)
    assert "admis(e) au SAS" not in client.get("/candidat/").get_data(as_text=True)
    c.sas, c.sas_class, c.sas_bedroom = Sas(name="SAS 2026"), "Classe A", "B-12"
    db.session.commit()
    html = client.get("/candidat/").get_data(as_text=True)
    assert "admis(e) au SAS" in html and "B-12" in html and "Classe A" in html and "attribution" in html  # equipment not yet given


def test_sas_is_admin_only(app, client, motivation_user):
    login(client, motivation_user)
    assert client.get("/admin/sas").status_code == 403
    assert client.post("/admin/sas/save", data={"name": "X"}).status_code == 403


def _admitted(db, sas, people):
    """people: [(name, gender)] -> candidates admitted to `sas`."""
    out = []
    for i, (name, gender) in enumerate(people):
        c = make_candidate(full_name=name, gender=gender, email=f"p{i}@test.ci", cni=f"C{i}")
        c.sas = sas
        out.append(c)
    db.session.commit()
    return out


def test_filters(app, db, client, admin):
    login(client, admin)
    sas = _sas(client)
    a, b, _ = _admitted(db, sas, [("ALPHA", "F"), ("BETA", "M"), ("GAMMA", "M")])
    a.sas_class, b.sas_bedroom = "Classe A", "CH-01"
    db.session.commit()
    names = lambda qs: {n for n in ("ALPHA", "BETA", "GAMMA") if n in client.get(f"/admin/sas/{sas.id}?{qs}").get_data(as_text=True)}
    assert names("class=Classe+A") == {"ALPHA"}
    assert names("class=none") == {"BETA", "GAMMA"}
    assert names("bedroom=set") == {"BETA"}
    assert names("gender=M&bedroom=none") == {"GAMMA"}
    assert names(f"q={a.matricule.lower()}") == {"ALPHA"}


def test_csv_template_and_import(app, db, client, admin):
    login(client, admin)
    sas = _sas(client)
    a, b = _admitted(db, sas, [("ALPHA; Jr", "F"), ("BETA", "M")])
    outsider = make_candidate(email="out@test.ci", cni="OUT")
    body = client.get(f"/admin/sas/{sas.id}/template").get_data(as_text=True)
    assert body.lstrip("\ufeff").startswith("matricule;email;nom;classe;chambre;materiel") and '"ALPHA; Jr"' in body
    csv = ("matricule;email;nom;classe;chambre;materiel\n"
           f"{a.matricule.lower()};;;Classe A;CH-01;Laptop 01\n"
           f";p1@test.ci;;Classe B;;\n"                      # by email, empty cells unchanged
           f"{outsider.matricule};;;Classe Z;;\n")           # not in this SAS
    b.sas_bedroom = "CH-09"
    db.session.commit()
    html = client.post(f"/admin/sas/{sas.id}/import", data={"file": (io.BytesIO(csv.encode()), "a.csv")},
                       content_type="multipart/form-data", follow_redirects=True).get_data(as_text=True)
    assert "2 candidat(s) mis à jour" in html and "pas admis dans ce SAS" in html
    db.session.expire_all()
    assert (a.sas_class, a.sas_bedroom, a.sas_equipment) == ("Classe A", "CH-01", "Laptop 01")
    assert (b.sas_class, b.sas_bedroom) == ("Classe B", "CH-09") and not outsider.sas_class


def test_auto_assign(app, db, client, admin):
    login(client, admin)
    sas = _sas(client)
    people = _admitted(db, sas, [(f"F{i}", "F") for i in range(3)] + [(f"M{i}", "M") for i in range(3)])
    people[0].sas_class, people[0].sas_bedroom, people[0].sas_equipment = "Classe X", "CH-01", "Laptop n°01"
    db.session.commit()
    client.post(f"/admin/sas/{sas.id}/auto-assign", data={
        "classes": "Classe A, Classe B", "bedroom_prefix": "CH-", "bedroom_start": "1", "bedroom_count": "3",
        "capacity": "2", "equipment": "Laptop n°{n}", "equipment_start": "1"})
    db.session.expire_all()
    assert people[0].sas_class == "Classe X" and people[0].sas_equipment == "Laptop n°01"  # already given: kept
    rest = people[1:]
    classes = [c.sas_class for c in rest]
    assert sorted(classes) == ["Classe A", "Classe A", "Classe B", "Classe B", "Classe B"] or \
        sorted(classes) == ["Classe A", "Classe A", "Classe A", "Classe B", "Classe B"]  # balanced
    rooms = {}
    for c in people:
        rooms.setdefault(c.sas_bedroom, set()).add(c.gender)
    assert all(len(g) == 1 for r, g in rooms.items() if r)  # never mixed
    assert sum(1 for c in people if not c.sas_bedroom) == 1  # 3 rooms x 2 places, F room CH-01 full: one M left out
    labels = [c.sas_equipment for c in people]
    assert len(set(labels)) == 6 and "Laptop n°02" in labels  # unique, numbering skips n°01 already given
