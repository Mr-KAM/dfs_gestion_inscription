import pytest

from app.models import AuditLog, Candidate
from app.services import candidate_service, settings_service
from app.services.candidate_service import BusinessError
from tests.conftest import login, make_candidate


def test_piece_number_required(app, motivation_user):
    c = make_candidate()
    with pytest.raises(BusinessError, match="numéro de pièce"):
        candidate_service.validate_motivation(c, "  ", "DFS-1", motivation_user)
    assert not c.motivation_completed


def test_validate_with_manual_code(app, db, motivation_user):
    c = make_candidate()
    candidate_service.validate_motivation(c, "P-42", "dfs-2026-0042", motivation_user)
    c = db.session.get(Candidate, c.id)
    assert c.motivation_completed and c.exam_code == "dfs-2026-0042" and c.piece_number == "P-42"
    assert c.motivation_completed_by == motivation_user.id and c.motivation_completed_at
    actions = {a.action for a in db.session.query(AuditLog)}
    assert {"motivation_validate", "exam_code_create"} <= actions


def test_invalid_code_format(app, motivation_user):
    with pytest.raises(BusinessError, match="invalide"):
        candidate_service.validate_motivation(make_candidate(), "P", "<script>", motivation_user)


def test_exam_code_unique(app, motivation_user):
    make_candidate(email="a@t.ci", cni="A", motivation_completed=True, exam_code="DFS-2026-0001", piece_number="1")
    c = make_candidate(email="b@t.ci", cni="B")
    with pytest.raises(BusinessError, match="déjà attribué"):
        candidate_service.validate_motivation(c, "P", "DFS-2026-0001", motivation_user)


def test_auto_generated_codes_are_sequential(app, motivation_user):
    settings_service.set_many({"exam_code_prefix": "DFS", "campaign_year": "2026"})
    first, second = make_candidate(email="a@t.ci", cni="A"), make_candidate(email="b@t.ci", cni="B")
    candidate_service.validate_motivation(first, "P1", "", motivation_user)
    candidate_service.validate_motivation(second, "P2", "", motivation_user)
    assert (first.exam_code, second.exam_code) == ("DFS-2026-0001", "DFS-2026-0002")


def test_motivation_tester_cannot_change_validated_candidate(app, motivation_user, admin):
    c = make_candidate()
    candidate_service.validate_motivation(c, "P1", "DFS-1", motivation_user)
    with pytest.raises(BusinessError, match="déjà validée"):
        candidate_service.validate_motivation(c, "P2", "DFS-2", motivation_user)
    candidate_service.validate_motivation(c, "P2", "DFS-2", admin)  # admin may correct
    assert c.exam_code == "DFS-2"  # still a single code per candidate


def test_http_validation_and_technical_visibility(client, app, db, motivation_user, technical_user):
    c = make_candidate()
    login(client, technical_user)
    assert client.get(f"/candidates/{c.id}").status_code == 404  # not eligible yet
    client.post("/logout")

    login(client, motivation_user)
    response = client.post(f"/motivation/{c.id}/validate", data={"piece_number": "P9", "exam_code": "DFS-X9"},
                           follow_redirects=True)
    assert "Motivation terminée" in response.get_data(as_text=True)
    client.post("/logout")

    login(client, technical_user)
    page = client.get("/candidates/").get_data(as_text=True)
    assert "DFS-X9" in page
    assert client.get(f"/candidates/{c.id}").status_code == 200
    assert client.get(f"/candidates/{c.id}/edit").status_code == 403


def test_copy_button_after_validation(client, motivation_user):
    c = make_candidate(full_name='KONE "Awa" <script>')
    login(client, motivation_user)
    assert "js-copy" not in client.get(f"/candidates/{c.id}").get_data(as_text=True)  # nothing saved yet
    page = client.post(f"/motivation/{c.id}/validate", data={"piece_number": "P-7", "exam_code": "dfs-Ab1"},
                       follow_redirects=True).get_data(as_text=True)
    assert ('data-copy="Nom et prénoms : KONE &#34;Awa&#34; &lt;script&gt;\n'
            'Numéro de pièce : P-7\nCode examen : dfs-Ab1"') in page


@pytest.mark.parametrize("role", ["admin", "supervisor", "motivation_tester", "technical_tester"])
def test_copy_button_in_candidate_list(client, app, role):
    from tests.conftest import make_user
    make_candidate(email="a@t.ci", cni="A", full_name="KONE Awa", motivation_completed=True,
                   exam_code="dfs-Ab1", piece_number="P-7")
    make_candidate(email="b@t.ci", cni="B", full_name="YAO Serge")  # not validated: no button
    login(client, make_user(role))
    page = client.get("/candidates/").get_data(as_text=True)
    expected = 'data-copy="Nom et prénoms : KONE Awa\nNuméro de pièce : P-7\nCode examen : dfs-Ab1"'
    assert page.count("js-copy") == 1 and expected in page
