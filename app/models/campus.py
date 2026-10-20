"""SAS campus life: notifications and exercises for a class, reception visits, student incidents.
A target class is (sas_id, class_name); class_name '' = the whole SAS."""
from app.extensions import db
from app.utils import as_utc, utcnow

SUBMITTED, VALIDATED, REJECTED = "submitted", "validated", "rejected"
SUBMISSION_BADGES = {SUBMITTED: ("info", "À corriger"), VALIDATED: ("success", "Validé"), REJECTED: ("danger", "À refaire")}

INCIDENT_CATEGORIES = {"discipline": "Discipline", "health": "Santé", "housing": "Logement", "equipment": "Matériel",
                       "other": "Autre"}


class Notification(db.Model):
    __tablename__ = "notifications"

    id = db.Column(db.Integer, primary_key=True)
    sas_id = db.Column(db.Integer, db.ForeignKey("sas.id", ondelete="CASCADE"), nullable=False, index=True)
    class_name = db.Column(db.String(50), nullable=False, default="")
    title = db.Column(db.String(150), nullable=False)
    body = db.Column(db.Text, nullable=False, default="")
    created_by = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"))
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)

    sas = db.relationship("Sas")
    author = db.relationship("User")


class Exercise(db.Model):
    __tablename__ = "exercises"

    id = db.Column(db.Integer, primary_key=True)
    sas_id = db.Column(db.Integer, db.ForeignKey("sas.id", ondelete="CASCADE"), nullable=False, index=True)
    class_name = db.Column(db.String(50), nullable=False, default="")
    title = db.Column(db.String(150), nullable=False)
    instructions = db.Column(db.Text, nullable=False, default="")
    opens_at = db.Column(db.DateTime(timezone=True), nullable=False)  # scheduled: hidden from students before
    due_at = db.Column(db.DateTime(timezone=True))
    created_by = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"))
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)

    sas = db.relationship("Sas")
    submissions = db.relationship("ExerciseSubmission", back_populates="exercise", cascade="all, delete-orphan")

    @property
    def is_published(self) -> bool:
        return as_utc(self.opens_at) <= utcnow()

    @property
    def accepts_answers(self) -> bool:
        return self.is_published and (self.due_at is None or utcnow() < as_utc(self.due_at))


class ExerciseSubmission(db.Model):
    __tablename__ = "exercise_submissions"

    id = db.Column(db.Integer, primary_key=True)
    exercise_id = db.Column(db.Integer, db.ForeignKey("exercises.id", ondelete="CASCADE"), nullable=False, index=True)
    candidate_id = db.Column(db.Integer, db.ForeignKey("candidates.id", ondelete="CASCADE"), nullable=False, index=True)
    answer = db.Column(db.Text, nullable=False, default="")
    submitted_at = db.Column(db.DateTime(timezone=True))
    status = db.Column(db.String(20), nullable=False, default=SUBMITTED)
    score = db.Column(db.String(20), default="")
    feedback = db.Column(db.Text, default="")
    reviewed_by = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"))
    reviewed_at = db.Column(db.DateTime(timezone=True))

    exercise = db.relationship("Exercise", back_populates="submissions")
    candidate = db.relationship("Candidate")

    __table_args__ = (db.UniqueConstraint("exercise_id", "candidate_id", name="uq_exercise_submissions_exercise_candidate"),)

    @property
    def badge(self) -> tuple[str, str]:
        return SUBMISSION_BADGES.get(self.status, ("secondary", self.status))


class Visit(db.Model):
    """Reception log: who came, for which student, when they left."""
    __tablename__ = "visits"

    id = db.Column(db.Integer, primary_key=True)
    visitor_name = db.Column(db.String(150), nullable=False)
    visitor_phone = db.Column(db.String(30), default="")
    candidate_id = db.Column(db.Integer, db.ForeignKey("candidates.id", ondelete="SET NULL"), index=True)
    reason = db.Column(db.String(255), default="")
    arrived_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    left_at = db.Column(db.DateTime(timezone=True))
    created_by = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"))

    candidate = db.relationship("Candidate")


class Incident(db.Model):
    """A problem with a student (discipline, health, housing...), followed until resolved."""
    __tablename__ = "incidents"

    id = db.Column(db.Integer, primary_key=True)
    candidate_id = db.Column(db.Integer, db.ForeignKey("candidates.id", ondelete="CASCADE"), nullable=False, index=True)
    category = db.Column(db.String(20), nullable=False, default="other")
    description = db.Column(db.Text, nullable=False)
    created_by = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"))
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    resolved_at = db.Column(db.DateTime(timezone=True))
    resolution = db.Column(db.Text, default="")
    resolved_by = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"))

    candidate = db.relationship("Candidate")
    author = db.relationship("User", foreign_keys=[created_by])

    @property
    def category_label(self) -> str:
        return INCIDENT_CATEGORIES.get(self.category, self.category)
