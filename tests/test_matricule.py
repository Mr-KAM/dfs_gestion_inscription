"""Candidate matricule: 26CAND000001, unique, increasing with insertion order, on every creation path."""
import io

from app.extensions import db
from app.models import Candidate
from app.services import candidate_service, import_service
from tests.conftest import login, make_candidate
from tests.test_import import HEADER, ROWS


def test_matricules_follow_order(app):
    prefix = candidate_service.matricule_prefix()
    assert prefix == f"{candidate_service.today():%y}CAND" and len(prefix) == 6
    first, second = make_candidate(email="a@t.ci", cni="A"), make_candidate(email="b@t.ci", cni="B")
    assert first.matricule == f"{prefix}000001" and second.matricule == f"{prefix}000002"
    assert len(first.matricule) == 12
    batch = [Candidate(full_name=f"Batch {i}") for i in range(3)]  # one flush, several candidates
    db.session.add_all(batch)
    db.session.commit()
    assert [c.matricule[-6:] for c in batch] == ["000003", "000004", "000005"]


def test_import_and_form_get_matricules(app, client, admin):
    login(client, admin)
    client.post("/candidates/new", data={"full_name": "Form Candidat"})
    client.post("/admin/imports", data={"file": (io.BytesIO((HEADER + "\n".join(ROWS)).encode()), "c.csv")},
                content_type="multipart/form-data")
    client.post("/admin/imports/confirm",
                data={f"map_{k}": v for k, v in import_service.auto_mapping(HEADER.strip().split(";")).items()})
    names = {c.full_name: c.matricule[-6:] for c in db.session.query(Candidate)}
    assert names == {"Form Candidat": "000001", "KONE Awa": "000002", "YAO Serge": "000003"}
    html = client.get("/candidates/?q=" + candidate_service.matricule_prefix() + "000003").get_data(as_text=True)
    assert "YAO Serge" in html and "KONE Awa" not in html
