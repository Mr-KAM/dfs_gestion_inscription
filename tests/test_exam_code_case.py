"""Exam codes are case-sensitive: stored and shown exactly as typed."""
import pytest

from app.extensions import db
from app.models import Candidate
from app.services import candidate_service, settings_service
from app.services.candidate_service import BusinessError
from tests.conftest import login, make_candidate


def test_lowercase_code_is_kept_as_typed(app, motivation_user):
    c = make_candidate()
    candidate_service.validate_motivation(c, "P1", "  dfs-2026-AbC  ", motivation_user)
    assert db.session.get(Candidate, c.id).exam_code == "dfs-2026-AbC"


def test_codes_differing_only_by_case_are_distinct(app, motivation_user):
    candidate_service.validate_motivation(make_candidate(email="a@t.ci", cni="A"), "P1", "abc-1", motivation_user)
    other = make_candidate(email="b@t.ci", cni="B")
    candidate_service.validate_motivation(other, "P2", "ABC-1", motivation_user)
    assert other.exam_code == "ABC-1"


def test_exact_duplicate_is_still_refused(app, motivation_user):
    candidate_service.validate_motivation(make_candidate(email="a@t.ci", cni="A"), "P1", "abc-1", motivation_user)
    with pytest.raises(BusinessError, match="déjà attribué"):
        candidate_service.validate_motivation(make_candidate(email="b@t.ci", cni="B"), "P2", "abc-1", motivation_user)


def test_generated_code_keeps_prefix_case(app, motivation_user):
    settings_service.set_many({"exam_code_prefix": "dfs", "campaign_year": "2026"})
    db.session.commit()
    c = make_candidate()
    candidate_service.validate_motivation(c, "P1", "", motivation_user)
    assert c.exam_code == "dfs-2026-0001"


def test_lowercase_code_rendered_as_typed(client, motivation_user):
    """The page must not restyle the code into capitals (no text-uppercase)."""
    c = make_candidate()
    login(client, motivation_user)
    page = client.post(f"/motivation/{c.id}/validate", data={"piece_number": "P1", "exam_code": "dfs-x9"},
                       follow_redirects=True).get_data(as_text=True)
    assert "code examen dfs-x9" in page and "<code>dfs-x9</code>" in page
    assert "text-uppercase" not in page


def test_search_by_code_finds_any_case(client, motivation_user):
    make_candidate(motivation_completed=True, exam_code="DFS-2026-0042", piece_number="1")
    login(client, motivation_user)
    assert "DFS-2026-0042" in client.get("/candidates/?q=dfs-2026-0042").get_data(as_text=True)
