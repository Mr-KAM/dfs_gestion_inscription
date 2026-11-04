"""SAS defenses (soutenances): juries of staff members, each hearing a list of SAS students."""
from datetime import timedelta

from app.extensions import db
from app.utils import as_utc, utcnow

JURY_ROLES = {"president": "Président", "technical": "Évaluateur technique",
              "presentation": "Évaluateur présentation"}


class Defense(db.Model):
    __tablename__ = "defenses"

    id = db.Column(db.Integer, primary_key=True)
    sas_id = db.Column(db.Integer, db.ForeignKey("sas.id", ondelete="CASCADE"), nullable=False, index=True)
    name = db.Column(db.String(150), nullable=False)
    starts_at = db.Column(db.DateTime(timezone=True))
    location = db.Column(db.String(150), default="")
    created_by = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"))
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)

    sas = db.relationship("Sas")
    juries = db.relationship("Jury", back_populates="defense", cascade="all, delete-orphan", order_by="Jury.id")


class Jury(db.Model):
    __tablename__ = "juries"

    id = db.Column(db.Integer, primary_key=True)
    defense_id = db.Column(db.Integer, db.ForeignKey("defenses.id", ondelete="CASCADE"), nullable=False, index=True)
    name = db.Column(db.String(100), nullable=False)
    room = db.Column(db.String(100), default="")
    starts_at = db.Column(db.DateTime(timezone=True))  # None: the defense's start
    duration = db.Column(db.Integer, nullable=False, default=30)  # minutes per student

    defense = db.relationship("Defense", back_populates="juries")
    members = db.relationship("JuryMember", back_populates="jury", cascade="all, delete-orphan",
                              order_by="JuryMember.id")
    candidates = db.relationship("JuryCandidate", back_populates="jury", cascade="all, delete-orphan",
                                 order_by="JuryCandidate.position")

    @property
    def start(self):
        return as_utc(self.starts_at or self.defense.starts_at)

    def slot(self, index: int):
        """Passage time of the index-th student (0-based), None without a start time."""
        return self.start + timedelta(minutes=self.duration * index) if self.start else None


class JuryMember(db.Model):
    """A staff member in a jury. defense_id is repeated so the database refuses one person in two juries."""
    __tablename__ = "jury_members"

    id = db.Column(db.Integer, primary_key=True)
    jury_id = db.Column(db.Integer, db.ForeignKey("juries.id", ondelete="CASCADE"), nullable=False, index=True)
    defense_id = db.Column(db.Integer, db.ForeignKey("defenses.id", ondelete="CASCADE"), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    role = db.Column(db.String(20), nullable=False, default="technical")

    jury = db.relationship("Jury", back_populates="members")
    user = db.relationship("User")

    __table_args__ = (db.UniqueConstraint("defense_id", "user_id", name="uq_jury_members_defense_user"),)

    @property
    def role_label(self) -> str:
        return JURY_ROLES.get(self.role, self.role)


class JuryCandidate(db.Model):
    """A student defending before a jury (one jury per student and defense), in passage order."""
    __tablename__ = "jury_candidates"

    id = db.Column(db.Integer, primary_key=True)
    jury_id = db.Column(db.Integer, db.ForeignKey("juries.id", ondelete="CASCADE"), nullable=False, index=True)
    defense_id = db.Column(db.Integer, db.ForeignKey("defenses.id", ondelete="CASCADE"), nullable=False)
    candidate_id = db.Column(db.Integer, db.ForeignKey("candidates.id", ondelete="CASCADE"), nullable=False,
                             index=True)
    position = db.Column(db.Integer, nullable=False, default=0)

    jury = db.relationship("Jury", back_populates="candidates")
    candidate = db.relationship("Candidate")

    __table_args__ = (db.UniqueConstraint("defense_id", "candidate_id", name="uq_jury_candidates_defense_candidate"),)
