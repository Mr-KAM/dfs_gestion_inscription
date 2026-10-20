from app.extensions import db
from app.utils import utcnow

# Internal physical-file status values (spec §11) and their French labels.
FILE_VALIDATED = "validated"
FILE_MISSING = "missing"
FILE_NOT_FOUND = "not_found"
FILE_PENDING = "pending"
FILE_STATUSES = {
    FILE_VALIDATED: "Oui",
    FILE_MISSING: "Non",
    FILE_NOT_FOUND: "Non trouvé",
    FILE_PENDING: "À vérifier",
}

# Kobo badge: (css class, label) derived from physical_file_status.
KOBO_BADGES = {
    FILE_VALIDATED: ("success", "Conforme"),
    FILE_MISSING: ("danger", "Non conforme"),
    FILE_NOT_FOUND: ("danger", "Non conforme"),
    FILE_PENDING: ("warning", "À vérifier"),
    None: ("secondary", "Non synchronisé"),
}


class Candidate(db.Model):
    __tablename__ = "candidates"

    id = db.Column(db.Integer, primary_key=True)
    # 26CAND000001: set automatically on insert (candidate_service._assign_matricules), never edited.
    matricule = db.Column(db.String(12), unique=True, nullable=False)
    candidate_number = db.Column(db.String(50), index=True)
    group_name = db.Column(db.String(50), index=True)
    scheduled_date = db.Column(db.Date, index=True)
    scheduled_time = db.Column(db.String(30))  # free text: "08:00", "8H-10H"...
    passage_order = db.Column(db.Integer)

    full_name = db.Column(db.String(200), nullable=False, index=True)
    birth_date = db.Column(db.Date)
    gender = db.Column(db.String(1))  # M / F
    email = db.Column(db.String(255), index=True)
    phone = db.Column(db.String(30), index=True)
    city = db.Column(db.String(120))
    cni = db.Column(db.String(50), index=True)
    # Profile photo uploaded by the candidate (JPEG / PNG / WebP); deferred: never loaded with lists.
    photo = db.deferred(db.Column(db.LargeBinary))
    photo_type = db.Column(db.String(20))  # mimetype, None = no photo

    physical_file_status = db.Column(db.String(20), index=True)

    piece_number = db.Column(db.String(50))
    # Rules 1 & 2: a single column + UNIQUE -> one code per candidate, one candidate per code.
    exam_code = db.Column(db.String(50), unique=True)

    motivation_completed = db.Column(db.Boolean, nullable=False, default=False, index=True)
    motivation_completed_at = db.Column(db.DateTime(timezone=True))
    motivation_completed_by = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"))

    technical_completed = db.Column(db.Boolean, nullable=False, default=False, index=True)
    technical_completed_at = db.Column(db.DateTime(timezone=True))

    # Jury verdict, ticked in the candidate list once the test is completed.
    motivation_passed = db.Column(db.Boolean, nullable=False, default=False, server_default=db.false())
    technical_passed = db.Column(db.Boolean, nullable=False, default=False, server_default=db.false())

    # SAS (next stage): admission, then class, bedroom and equipment given by the admin.
    sas_id = db.Column(db.Integer, db.ForeignKey("sas.id", ondelete="SET NULL"), index=True)
    sas_admitted_at = db.Column(db.DateTime(timezone=True))
    sas_class = db.Column(db.String(50), default="")
    sas_bedroom = db.Column(db.String(50), default="")
    sas_equipment = db.Column(db.String(255), default="")

    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)

    sas = db.relationship("Sas", back_populates="members")
    motivation_tester = db.relationship("User", foreign_keys=[motivation_completed_by])
    sessions = db.relationship(
        "TechnicalTestSession", back_populates="candidate", cascade="all, delete-orphan",
        order_by="TechnicalTestSession.created_at",
    )
    # Login of the candidate's own space (deleted with the candidate).
    account = db.relationship("User", back_populates="candidate", uselist=False, cascade="all, delete-orphan",
                              foreign_keys="User.candidate_id")
    kobo_submissions = db.relationship(
        "KoboSubmission", back_populates="candidate", order_by="KoboSubmission.submitted_at",
    )

    __table_args__ = (
        db.CheckConstraint(
            "physical_file_status IS NULL OR physical_file_status IN ('validated', 'missing', 'not_found', 'pending')",
            name="physical_file_status",
        ),
        db.CheckConstraint("NOT motivation_completed OR exam_code IS NOT NULL", name="motivation_has_code"),
        db.CheckConstraint("NOT technical_completed OR motivation_completed", name="technical_after_motivation"),
    )

    # ---- derived state (spec §56: never stored twice) ----
    @property
    def kobo_badge(self) -> tuple[str, str]:
        return KOBO_BADGES.get(self.physical_file_status, KOBO_BADGES[None])

    @property
    def latest_submission(self):
        return self.kobo_submissions[-1] if self.kobo_submissions else None

    @property
    def latest_session(self):
        return self.sessions[-1] if self.sessions else None

    @property
    def active_session(self):
        session = self.latest_session
        return session if session and session.is_active else None

    @property
    def technical_badge(self) -> tuple[str, str]:
        if not self.motivation_completed:
            return "secondary", "Non éligible"
        if self.technical_completed:
            return "success", "Terminé"
        session = self.latest_session
        if session is None:
            return "info", "En attente"
        return session.status_badge

    @property
    def global_status(self) -> str:
        if self.technical_completed:
            return "Technique terminée"
        if self.active_session:
            return "Technique en cours"
        if self.motivation_completed:
            return "Éligible technique"
        if self.physical_file_status == FILE_VALIDATED:
            return "Dossier vérifié"
        if self.kobo_submissions:
            return "Présent"
        return "Planifié"
