"""Library, open to every account: read the digital books, ask for the physical ones. The librarian (and the admin)
keeps the catalogue, validates the requests, records the returns and follows the borrowers."""
from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.extensions import db
from app.models import Book, BookLoan, User
from app.models.library import BOOK_KINDS
from app.models.user import LIBRARY_ROLES
from app.services import library_service
from app.services.candidate_service import BusinessError, today

bp = Blueprint("library", __name__, url_prefix="/bibliotheque")


@bp.before_request
@login_required
def require_login():
    return None


def _require_librarian() -> None:
    if current_user.role not in LIBRARY_ROLES:
        abort(403)


def _run(action, success: str) -> bool:
    try:
        action()
        flash(success, "success")
        return True
    except BusinessError as exc:
        db.session.rollback()
        flash(str(exc), "danger")
        return False


@bp.get("/")
def index():
    q, kind = request.args.get("q", "").strip(), request.args.get("kind", "")
    mine = sorted(current_user.book_loans, key=lambda loan: loan.requested_at, reverse=True)
    return render_template("library/index.html", books=library_service.catalogue(q, kind), q=q, kind=kind,
                           kinds=BOOK_KINDS, mine=mine, today=today(),
                           open_ids={loan.book_id for loan in mine if loan.status in ("requested", "borrowed")})


@bp.post("/livres/<int:book_id>/demander")
def request_loan(book_id: int):
    book = db.get_or_404(Book, book_id)
    _run(lambda: library_service.request_loan(book, current_user),
         f"Demande envoyée pour « {book.title} » : le responsable de la bibliothèque va la traiter.")
    return redirect(url_for("library.index"))


@bp.post("/demandes/<int:loan_id>/annuler")
def cancel(loan_id: int):
    loan = db.get_or_404(BookLoan, loan_id)
    _run(lambda: library_service.cancel(loan, current_user), "Demande annulée.")
    return redirect(url_for("library.index"))


# ---- librarian

@bp.route("/gestion", methods=["GET", "POST"])
def manage():
    _require_librarian()
    if request.method == "POST":
        if _run(lambda: library_service.save_book(None, request.form), "Livre ajouté au catalogue."):
            return redirect(url_for("library.manage"))
    return render_template("library/manage.html", kinds=BOOK_KINDS, today=today(),
                           requested=library_service.loans("requested"), borrowed=library_service.loans("borrowed"),
                           books=library_service.catalogue(), readers=library_service.readers())


@bp.route("/livres/<int:book_id>", methods=["GET", "POST"])
def book(book_id: int):
    _require_librarian()
    b = db.get_or_404(Book, book_id)
    if request.method == "POST":
        if request.form.get("action") == "delete":
            title = b.title
            if _run(lambda: library_service.delete_book(b), f"« {title} » supprimé du catalogue."):
                return redirect(url_for("library.manage"))
        else:
            _run(lambda: library_service.save_book(b, request.form), "Livre enregistré.")
        return redirect(url_for("library.book", book_id=b.id))
    return render_template("library/book.html", b=b, kinds=BOOK_KINDS, today=today())


@bp.post("/emprunts/<int:loan_id>")
def loan(loan_id: int):
    """Librarian: accept / refuse a request, or record the return of a borrowed copy."""
    _require_librarian()
    loan = db.get_or_404(BookLoan, loan_id)
    action = request.form.get("action")
    if action == "return":
        _run(lambda: library_service.give_back(loan), f"Retour enregistré : « {loan.book.title} ».")
    elif action in ("accept", "refuse"):
        _run(lambda: library_service.decide(loan, action == "accept", request.form.get("due_on", ""),
                                            request.form.get("note", ""), current_user),
             f"Emprunt validé : « {loan.book.title} » pour {loan.user.display_name}." if action == "accept"
             else "Demande refusée.")
    return redirect(request.form.get("next") or url_for("library.manage"))


@bp.route("/lecteurs/<int:user_id>", methods=["GET", "POST"])
def reader(user_id: int):
    """One borrower: his loans, his email and phone, a Zeus notification if he is a registered student."""
    _require_librarian()
    user = db.get_or_404(User, user_id)
    if request.method == "POST":
        _run(lambda: library_service.notify(user, request.form, current_user), "Notification envoyée dans Zeus.")
        return redirect(url_for("library.reader", user_id=user.id))
    loans = sorted(user.book_loans, key=lambda loan: loan.requested_at, reverse=True)
    return render_template("library/reader.html", reader=user, loans=loans, today=today(),
                           contact=library_service.contact(user))
