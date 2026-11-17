"""Library: digital books by link, physical books on request; the librarian validates, records returns, follows
each borrower and writes to him in Zeus (or has his email and phone)."""
from app.models import Book, BookLoan, Cohort, ZeusNotification
from tests.conftest import login, make_user
from tests.test_portal import portal_login
from tests.test_zeus import _student


def test_library_flow(app, db, client):
    librarian = make_user("librarian", "biblio@test.ci")
    coach = make_user("coach", "coach@test.ci")
    student = _student(db, Cohort(name="2026-2027"), "Ama", "26DFS000001")

    login(client, librarian)
    assert client.get("/").headers["Location"].endswith("/bibliotheque/gestion")
    client.post("/bibliotheque/gestion", data={"kind": "digital", "title": "Archives 2025"})  # no link: refused
    client.post("/bibliotheque/gestion", data={"kind": "digital", "title": "Archives 2025",
                                               "url": "https://archives.example/2025.pdf"})
    client.post("/bibliotheque/gestion", data={"kind": "physical", "title": "Agronomie tropicale", "copies": "1"})
    assert [b.title for b in db.session.query(Book).order_by(Book.id)] == ["Archives 2025", "Agronomie tropicale"]
    book = db.session.query(Book).filter_by(kind="physical").one()
    client.post("/logout")

    login(client, coach)
    assert client.get("/bibliotheque/gestion").status_code == 403  # the librarian (and the admin) only
    html = client.get("/bibliotheque/").get_data(as_text=True)
    assert "https://archives.example/2025.pdf" in html and "1 / 1 disponible(s)" in html
    client.post(f"/bibliotheque/livres/{book.id}/demander")
    client.post("/logout")
    portal_login(client, student.candidate.email)
    client.post(f"/bibliotheque/livres/{book.id}/demander")
    client.post(f"/bibliotheque/livres/{book.id}/demander")  # already asked: not twice
    client.post("/logout")
    coach_loan, student_loan = db.session.query(BookLoan).order_by(BookLoan.id).all()
    assert db.session.query(BookLoan).count() == 2

    login(client, librarian)
    client.post(f"/bibliotheque/emprunts/{student_loan.id}", data={"action": "accept", "due_on": "2099-01-31"})
    client.post(f"/bibliotheque/emprunts/{coach_loan.id}", data={"action": "accept"})  # no copy left
    db.session.refresh(book)
    assert (student_loan.status, coach_loan.status, book.available) == ("borrowed", "requested", 0)
    html = client.get(f"/bibliotheque/livres/{book.id}").get_data(as_text=True)
    assert "Historique des emprunts" in html and "retour prévu le 31/01/2099" in html

    page = client.get(f"/bibliotheque/lecteurs/{student.candidate.account.id}").get_data(as_text=True)
    assert student.candidate.account.email in page and student.candidate.phone in page and "Envoyer dans Zeus" in page
    client.post(f"/bibliotheque/lecteurs/{student.candidate.account.id}",
                data={"title": "Bibliothèque : rappel de retour", "body": "Merci."})
    n = db.session.query(ZeusNotification).one()
    assert (n.enrollment_id, n.title) == (student.id, "Bibliothèque : rappel de retour")
    assert "Pas d'espace Zeus" in client.get(f"/bibliotheque/lecteurs/{coach.id}").get_data(as_text=True)

    client.post(f"/bibliotheque/emprunts/{student_loan.id}", data={"action": "return"})
    client.post(f"/bibliotheque/emprunts/{coach_loan.id}", data={"action": "accept"})  # the copy is back
    db.session.refresh(student_loan)
    db.session.refresh(coach_loan)
    assert student_loan.status == "returned" and student_loan.returned_at and coach_loan.status == "borrowed"
    assert "rendu le" in client.get(f"/bibliotheque/livres/{book.id}").get_data(as_text=True)


def test_library_opens_in_the_interface_one_comes_from(app, db, client, admin):
    login(client, admin)
    client.get("/zeus/")
    html = client.get("/bibliotheque/gestion").get_data(as_text=True)
    assert 'aria-label="Menu Zeus"' in html and "Gestion bibliothèque" in html  # from Zeus: in Zeus
    client.get("/?classique=1")
    assert 'aria-label="Menu Zeus"' not in client.get("/bibliotheque/").get_data(as_text=True)  # classic: classic
