"""Library: digital books (a link, open to all) and physical books (copies lent on request)."""
from app.extensions import db
from app.utils import utcnow

BOOK_KINDS = {"digital": "Numérique", "physical": "Physique"}
LOAN_STATUSES = {"requested": ("warning", "Demandé"), "refused": ("secondary", "Refusé"),
                 "borrowed": ("info", "Emprunté"), "returned": ("success", "Rendu")}


class Book(db.Model):
    __tablename__ = "books"

    id = db.Column(db.Integer, primary_key=True)
    kind = db.Column(db.String(10), nullable=False)
    title = db.Column(db.String(200), nullable=False)
    author = db.Column(db.String(200), nullable=False, default="")
    category = db.Column(db.String(100), nullable=False, default="")
    reference = db.Column(db.String(50), nullable=False, default="")  # cote / ISBN
    description = db.Column(db.Text, nullable=False, default="")
    url = db.Column(db.String(500), nullable=False, default="")       # digital: where to read it
    copies = db.Column(db.Integer, nullable=False, default=1)         # physical: copies that can be lent
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)

    loans = db.relationship("BookLoan", back_populates="book", cascade="all, delete-orphan",
                            order_by="BookLoan.requested_at.desc()")

    __table_args__ = (db.CheckConstraint("kind IN ('digital', 'physical')", name="kind"),
                      db.CheckConstraint("copies >= 0", name="copies"))

    @property
    def is_digital(self) -> bool:
        return self.kind == "digital"

    @property
    def available(self) -> int:
        return self.copies - sum(1 for loan in self.loans if loan.status == "borrowed")


class BookLoan(db.Model):
    """Request of a physical book -> refused, or borrowed (lent) -> returned."""
    __tablename__ = "book_loans"

    id = db.Column(db.Integer, primary_key=True)
    book_id = db.Column(db.Integer, db.ForeignKey("books.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    status = db.Column(db.String(10), nullable=False, default="requested")
    requested_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    decided_at = db.Column(db.DateTime(timezone=True))
    decided_by = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"))
    borrowed_at = db.Column(db.DateTime(timezone=True))
    due_on = db.Column(db.Date)
    returned_at = db.Column(db.DateTime(timezone=True))
    note = db.Column(db.String(300), nullable=False, default="")

    book = db.relationship("Book", back_populates="loans")
    user = db.relationship("User", foreign_keys=[user_id], backref=db.backref("book_loans", passive_deletes=True))
    decider = db.relationship("User", foreign_keys=[decided_by])

    __table_args__ = (db.CheckConstraint("status IN ('requested', 'refused', 'borrowed', 'returned')",
                                         name="status"),)

    @property
    def badge(self) -> tuple[str, str]:
        return LOAN_STATUSES[self.status]

    def is_late(self, today) -> bool:
        return self.status == "borrowed" and self.due_on is not None and self.due_on < today
