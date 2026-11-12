"""Training cohorts (academic year): SAS students admitted at the defense, registered at the desk."""
from app.extensions import db
from app.utils import utcnow


class Cohort(db.Model):
    __tablename__ = "cohorts"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False)  # academic year: "2026-2027"
    description = db.Column(db.String(255), default="")
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)

    enrollments = db.relationship("Enrollment", back_populates="cohort", cascade="all, delete-orphan",
                                  order_by="Enrollment.id")
    coaches = db.relationship("CohortCoach", back_populates="cohort", cascade="all, delete-orphan",
                              order_by="CohortCoach.id")


# Suggested in the form; the admin may type any other role.
COACH_ROLE_SUGGESTIONS = ("Coach référent", "Responsable pédagogique", "Coach technique", "Coach métier", "Tuteur")


class CohortCoach(db.Model):
    """A coach given access to a training cohort by the admin, with his role in it."""
    __tablename__ = "cohort_coaches"

    id = db.Column(db.Integer, primary_key=True)
    cohort_id = db.Column(db.Integer, db.ForeignKey("cohorts.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    role = db.Column(db.String(100), nullable=False, default="")

    cohort = db.relationship("Cohort", back_populates="coaches")
    user = db.relationship("User", backref=db.backref("cohort_roles", cascade="all, delete-orphan"))

    __table_args__ = (db.UniqueConstraint("cohort_id", "user_id", name="uq_cohort_coaches_cohort_user"),)


class Enrollment(db.Model):
    """A student made eligible by the admin; registered (DFS matricule) once the desk validates it.
    One training cohort per student: the DFS matricule is his for good."""
    __tablename__ = "cohort_enrollments"

    id = db.Column(db.Integer, primary_key=True)
    cohort_id = db.Column(db.Integer, db.ForeignKey("cohorts.id", ondelete="CASCADE"), nullable=False, index=True)
    candidate_id = db.Column(db.Integer, db.ForeignKey("candidates.id", ondelete="CASCADE"), nullable=False,
                             unique=True)
    # 26DFS000001: given at validation, in validation order (cohort_service.validate), never edited.
    matricule = db.Column(db.String(12), unique=True)
    arrival_date = db.Column(db.Date)
    bedroom = db.Column(db.String(50), default="")
    equipment = db.Column(db.String(255), default="")
    # Drawn by the student at the desk (PNG); deferred: never loaded with lists.
    signature = db.deferred(db.Column(db.LargeBinary))
    signature_type = db.Column(db.String(20))
    validated_at = db.Column(db.DateTime(timezone=True))
    validated_by = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"))
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)

    cohort = db.relationship("Cohort", back_populates="enrollments")
    candidate = db.relationship("Candidate", backref=db.backref("enrollment", uselist=False))
    validator = db.relationship("User", foreign_keys=[validated_by])

    __table_args__ = (db.CheckConstraint("(validated_at IS NULL) = (matricule IS NULL)", name="validated_matricule"),)
