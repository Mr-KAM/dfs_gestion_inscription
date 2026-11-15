"""Library: the librarian keeps the catalogue (digital books: a link; physical books: copies), validates or refuses
the loan requests, records the returns, follows each borrower and writes to him (Zeus) or calls / emails him."""
from datetime import date

from sqlalchemy import or_, select

from app.extensions import db
from app.models import Book, BookLoan, User, ZeusNotification
from app.models.library import BOOK_KINDS
from app.services import audit_service, zeus_service
from app.services.candidate_service import BusinessError, today
from app.utils import utcnow


def catalogue(q: str = "", kind: str = "") -> list[Book]:
    stmt = select(Book).order_by(Book.title)
    if kind in BOOK_KINDS:
        stmt = stmt.where(Book.kind == kind)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(Book.title.ilike(like), Book.author.ilike(like), Book.category.ilike(like),
                              Book.reference.ilike(like)))
    return db.session.scalars(stmt).all()


def save_book(book: Book | None, form) -> Book:
    kind = book.kind if book else form.get("kind", "")
    if kind not in BOOK_KINDS:
        raise BusinessError("Choisissez le type : numérique ou physique.")
    title = form.get("title", "").strip()[:200]
    url = form.get("url", "").strip()[:500]
    if not title:
        raise BusinessError("Le titre est obligatoire.")
    if kind == "digital" and not url.startswith(("https://", "http://")):
        raise BusinessError("Un livre numérique a besoin de son lien (https://…).")
    copies = form.get("copies", 1, type=int) if kind == "physical" else 0
    if copies is None or copies < 0:
        raise BusinessError("Nombre d'exemplaires invalide.")
    book = book or Book(kind=kind)
    if kind == "physical" and book.id and copies < book.copies - book.available:
        raise BusinessError("Des exemplaires sont empruntés : enregistrez d'abord leur retour.")
    book.title, book.url, book.copies = title, url if kind == "digital" else "", copies
    book.author = form.get("author", "").strip()[:200]
    book.category = form.get("category", "").strip()[:100]
    book.reference = form.get("reference", "").strip()[:50]
    book.description = form.get("description", "").strip()[:5000]
    db.session.add(book)
    db.session.flush()
    audit_service.log("library_update", book, {"title": title})
    db.session.commit()
    return book


def delete_book(book: Book) -> None:
    if any(loan.status == "borrowed" for loan in book.loans):
        raise BusinessError("Ce livre est emprunté : enregistrez d'abord son retour.")
    audit_service.log("library_update", book, {"deleted": book.title})
    db.session.delete(book)
    db.session.commit()


def open_loan(book: Book, user) -> BookLoan | None:
    return next((loan for loan in book.loans if loan.user_id == user.id and loan.status in ("requested", "borrowed")),
                None)


def request_loan(book: Book, user) -> BookLoan:
    if book.is_digital:
        raise BusinessError("Un livre numérique se lit directement par son lien.")
    if open_loan(book, user):
        raise BusinessError("Vous avez déjà une demande ou un emprunt en cours pour ce livre.")
    loan = BookLoan(book=book, user_id=user.id)
    db.session.add(loan)
    db.session.flush()
    audit_service.log("library_loan", loan, {"requested": book.title})
    db.session.commit()
    return loan


def cancel(loan: BookLoan, user) -> None:
    if loan.user_id != user.id or loan.status != "requested":
        raise BusinessError("Seule une demande en attente peut être annulée.")
    db.session.delete(loan)
    db.session.commit()


def decide(loan: BookLoan, accept: bool, due_on: str, note: str, user) -> None:
    """Accept (the copy is handed over: borrowed now, to give back by due_on) or refuse a request."""
    if loan.status != "requested":
        raise BusinessError("Cette demande a déjà été traitée.")
    if accept and loan.book.available < 1:
        raise BusinessError("Aucun exemplaire disponible : attendez un retour.")
    try:
        due = date.fromisoformat(due_on) if due_on else None
    except ValueError:
        raise BusinessError("Date de retour invalide.")
    if due and due < today():
        raise BusinessError("La date de retour prévue est passée.")
    loan.status = "borrowed" if accept else "refused"
    loan.decided_at, loan.decided_by, loan.note = utcnow(), user.id, note.strip()[:300]
    if accept:
        loan.borrowed_at, loan.due_on = loan.decided_at, due
    audit_service.log("library_loan", loan, {loan.status: loan.book.title, "user": loan.user.email})
    db.session.commit()


def give_back(loan: BookLoan) -> None:
    if loan.status != "borrowed":
        raise BusinessError("Ce livre n'est pas emprunté.")
    loan.status, loan.returned_at = "returned", utcnow()
    audit_service.log("library_loan", loan, {"returned": loan.book.title, "user": loan.user.email})
    db.session.commit()


def loans(status: str) -> list[BookLoan]:
    order = BookLoan.requested_at if status == "requested" else BookLoan.borrowed_at
    return db.session.scalars(select(BookLoan).where(BookLoan.status == status).order_by(order)).all()


def readers() -> list[dict]:
    """Every user who asked for or borrowed a book: his counts, most loans first."""
    day, rows = today(), {}
    for loan in db.session.scalars(select(BookLoan)):
        r = rows.setdefault(loan.user_id, {"user": loan.user, "total": 0, "borrowed": 0, "late": 0, "requested": 0})
        r["total"] += 1
        r["borrowed"] += loan.status == "borrowed"
        r["requested"] += loan.status == "requested"
        r["late"] += loan.is_late(day)
    return sorted(rows.values(), key=lambda r: (-r["borrowed"], -r["total"], r["user"].display_name))


def contact(user: User) -> dict:
    """Email, phone (a student's is on his file) and, for a registered student, his Zeus enrollment."""
    phone = user.phone or (user.candidate.phone if user.candidate else "")
    return {"email": user.email, "phone": phone or "", "enrollment": zeus_service.student_enrollment(user)}


def notify(user: User, form, sender) -> ZeusNotification:
    e = zeus_service.student_enrollment(user)
    if e is None:
        raise BusinessError("Cette personne n'a pas d'espace Zeus : contactez-la par email ou téléphone.")
    title = form.get("title", "").strip()[:150]
    if not title:
        raise BusinessError("Le titre est obligatoire.")
    n = ZeusNotification(cohort=e.cohort, enrollment=e, title=title, body=form.get("body", "").strip()[:5000],
                         created_by=sender.id)
    db.session.add(n)
    db.session.flush()
    audit_service.log("library_loan", n, {"notification": title, "to": user.email})
    db.session.commit()
    return n
