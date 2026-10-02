import io
from datetime import date

import pandas as pd
import pytest

from app.models import Candidate
from app.services import import_service
from tests.conftest import login, make_candidate

HEADER = "N°;Groupe;Date;Heure;Ordre;Nom et prenoms;Date de naissance;Sexe;Email;Telephone;Ville;CNI\n"
ROWS = [
    "1;G1;15/09/2026;08:00;1;KONE Awa;12/03/2001;F;awa@test.ci;07 07 07 07 01;Abidjan;CI100",
    "2;G1;15/09/2026;08:00;2;YAO Serge;01/01/2000;Masculin;serge@test.ci;+225 0505050502;Bouaké;CI101",
]


def write(tmp_path, name: str, content: str) -> str:
    path = tmp_path / name
    path.write_text(content, encoding="utf-8")
    return str(path)


def test_auto_mapping_recognises_variants():
    mapping = import_service.auto_mapping(["No", "Nom et prénoms", "Téléphone", "nom_prenoms", "Numéro"])
    assert mapping["candidate_number"] == "No"
    assert mapping["full_name"] == "Nom et prénoms"
    assert mapping["phone"] == "Téléphone"


def test_csv_valid_import(app, db, tmp_path):
    path = write(tmp_path, "c.csv", HEADER + "\n".join(ROWS))
    preview = import_service.preview(path)
    assert preview["total"] == 2 and preview["valid"] == 2 and preview["errors"] == 0
    stats = import_service.apply(path, preview["mapping"], {}, "c.csv")
    assert stats["created"] == 2
    serge = db.session.query(Candidate).filter_by(email="serge@test.ci").one()
    assert serge.phone == "0505050502" and serge.gender == "M"
    assert serge.scheduled_date == date(2026, 9, 15) and serge.birth_date == date(2000, 1, 1)
    assert serge.passage_order == 2


def test_excel_valid_import(app, db, tmp_path):
    path = tmp_path / "c.xlsx"
    pd.DataFrame([{
        "N°": 1, "Groupe": "G2", "Date": pd.Timestamp("2026-09-16"), "Heure": "10:00", "Ordre": 3,
        "Nom et prénoms": "TOURE Mariam", "Date de naissance": pd.Timestamp("1999-05-04"), "Sexe": "F",
        "Email": "mariam@test.ci", "Téléphone": 707070703, "Ville": "Yamoussoukro", "CNI": "ci102",
    }]).to_excel(path, index=False)
    preview = import_service.preview(str(path))
    assert preview["valid"] == 1, preview["rows"][0].errors
    import_service.apply(str(path), preview["mapping"], {}, "c.xlsx")
    c = db.session.query(Candidate).one()
    assert c.phone == "0707070703"  # leading zero restored
    assert c.scheduled_date == date(2026, 9, 16) and c.passage_order == 3 and c.cni == "CI102"


def test_missing_required_column(app, tmp_path):
    path = write(tmp_path, "c.csv", "Groupe;Email\nG1;a@b.ci\n")
    preview = import_service.preview(path)
    assert preview["missing"] == ["Nom et prénoms"]
    with pytest.raises(import_service.ImportFileError):
        import_service.apply(path, preview["mapping"], {}, "c.csv")


def test_invalid_rows_are_reported_not_imported(app, db, tmp_path):
    path = write(tmp_path, "c.csv", HEADER + ";G1;99/99/2026;;x;;;;bad-email;;;\n" + ROWS[0])
    preview = import_service.preview(path)
    assert preview["errors"] == 1 and preview["valid"] == 1
    assert any("Nom et prénoms manquant" in e for e in preview["rows"][0].errors)
    assert import_service.apply(path, preview["mapping"], {}, "c.csv")["created"] == 1


def test_duplicates_detected_and_actions(app, db, tmp_path):
    existing = make_candidate(full_name="KONE Awa", email="awa@test.ci", phone="0102030405", cni="OLD", city="Old")
    other = make_candidate(full_name="X", email="x@test.ci", phone="0909090909", cni="CI101")
    path = write(tmp_path, "c.csv", HEADER + "\n".join(ROWS))
    preview = import_service.preview(path)
    assert preview["duplicates"] == 2 and preview["valid"] == 0
    assert preview["rows"][0].duplicate_of.id == existing.id and preview["rows"][0].duplicate_reason == "email"
    assert preview["rows"][1].duplicate_of.id == other.id and preview["rows"][1].duplicate_reason == "CNI"

    stats = import_service.apply(path, preview["mapping"], {2: "update", 3: "review"}, "c.csv")
    assert stats["updated"] == 1 and len(stats["review"]) == 1 and stats["created"] == 0
    assert db.session.query(Candidate).count() == 2  # never silently duplicated
    assert db.session.get(Candidate, existing.id).city == "Abidjan"
    assert db.session.get(Candidate, other.id).full_name == "X"  # "review" left it untouched


def test_duplicate_inside_file(app, tmp_path):
    path = write(tmp_path, "c.csv", HEADER + ROWS[0] + "\n" + ROWS[0].replace("KONE Awa", "KONE A."))
    preview = import_service.preview(path)
    assert preview["valid"] == 1 and preview["errors"] == 1
    assert "Doublon dans le fichier" in preview["rows"][1].errors[0]


def test_http_import_flow(client, admin, db):
    login(client, admin)
    data = {"file": (io.BytesIO((HEADER + "\n".join(ROWS)).encode()), "candidats.csv")}
    assert client.post("/admin/imports", data=data, content_type="multipart/form-data").status_code == 302
    page = client.get("/admin/imports/preview").get_data(as_text=True)
    assert "candidats.csv" in page and "Confirmer l" in page
    form = {f"map_{k}": v for k, v in import_service.auto_mapping(HEADER.strip().split(";")).items()}
    assert client.post("/admin/imports/confirm", data=form).status_code == 200
    assert db.session.query(Candidate).count() == 2


def test_upload_rejects_bad_extension(client, admin):
    login(client, admin)
    data = {"file": (io.BytesIO(b"x"), "evil.exe")}
    response = client.post("/admin/imports", data=data, content_type="multipart/form-data", follow_redirects=True)
    assert "Format non supporté" in response.get_data(as_text=True)
