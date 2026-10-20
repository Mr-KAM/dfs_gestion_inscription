import hashlib
import hmac

from flask_login import UserMixin
from werkzeug.security import check_password_hash, generate_password_hash

from app.extensions import db, login_manager
from app.utils import utcnow

ROLE_ADMIN = "admin"
ROLE_MOTIVATION = "motivation_tester"
ROLE_TECHNICAL = "technical_tester"
ROLE_SUPERVISOR = "supervisor"
ROLE_COACH = "coach"            # permanent instructor: roll calls, notifications, exercises for his classes
ROLE_RECEPTION = "reception"    # reception: visits, absences, problems with students
ROLE_RESIDENCE = "residence"    # residence: bedrooms and equipment
ROLES = {
    ROLE_ADMIN: "Administrateur",
    ROLE_SUPERVISOR: "Superviseur",
    ROLE_MOTIVATION: "Testeur motivation",
    ROLE_TECHNICAL: "Testeur technique",
    ROLE_COACH: "Coach technique",
    ROLE_RECEPTION: "Responsable accueil",
    ROLE_RESIDENCE: "Responsable de cité",
}
# Who may do what. The supervisor combines both tester profiles, without any admin right.
MOTIVATION_ROLES = (ROLE_ADMIN, ROLE_SUPERVISOR, ROLE_MOTIVATION)
TECHNICAL_ROLES = (ROLE_ADMIN, ROLE_SUPERVISOR, ROLE_TECHNICAL)
RECRUITMENT_ROLES = (ROLE_ADMIN, ROLE_SUPERVISOR, ROLE_MOTIVATION, ROLE_TECHNICAL)  # candidate list / files
CAMPUS_ROLES = (ROLE_ADMIN, ROLE_COACH, ROLE_RECEPTION, ROLE_RESIDENCE)  # SAS life (/campus)

# Candidates log in to their own space only; never part of ROLES / ALL_ROLES.
ROLE_CANDIDATE = "candidate"
ROLE_LABELS = {**ROLES, ROLE_CANDIDATE: "Candidat"}


class User(UserMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True, nullable=False, index=True)
    full_name = db.Column(db.String(200), nullable=False, default="")
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(30), nullable=False)
    active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    last_login_at = db.Column(db.DateTime(timezone=True))
    # Set for accounts created with the shared default password: forces a change at first login.
    must_change_password = db.Column(db.Boolean, nullable=False, default=False, server_default=db.false())
    # use_alter: users <-> candidates reference each other (motivation_completed_by), so this FK is added last.
    candidate_id = db.Column(db.Integer, db.ForeignKey("candidates.id", ondelete="CASCADE", use_alter=True), unique=True)

    # Own profile (any staff member): phone + photo (JPEG / PNG / WebP, deferred: never loaded with lists).
    phone = db.Column(db.String(30))
    photo = db.deferred(db.Column(db.LargeBinary))
    photo_type = db.Column(db.String(20))
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow,
                           server_default=db.func.now())
    # Coach: the classes he teaches, as "sas_id:class name" strings.
    coach_classes = db.Column(db.JSON, nullable=False, default=list, server_default="[]")

    candidate = db.relationship("Candidate", back_populates="account", foreign_keys=[candidate_id])

    __table_args__ = (
        db.CheckConstraint(
            "role IN (" + ", ".join(f"'{r}'" for r in (*ROLES, ROLE_CANDIDATE)) + ")",
            name="role",
        ),
        # A candidate account is always tied to exactly one candidate; staff accounts never are.
        db.CheckConstraint(f"(role = '{ROLE_CANDIDATE}') = (candidate_id IS NOT NULL)", name="candidate_link"),
    )

    def set_password(self, password: str) -> None:
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)

    @property
    def is_active(self) -> bool:  # Flask-Login refuses inactive users
        return self.active

    @property
    def role_label(self) -> str:
        return ROLE_LABELS.get(self.role, self.role)

    @property
    def is_candidate(self) -> bool:
        return self.role == ROLE_CANDIDATE

    @property
    def display_name(self) -> str:
        return self.full_name or self.email

    def has_role(self, *roles: str) -> bool:
        return self.role in roles

    @property
    def session_fingerprint(self) -> str:
        """Changes with the password: a reset or change logs out every other open session."""
        return hashlib.sha256(self.password_hash.encode()).hexdigest()[:16]

    def get_id(self) -> str:
        return f"{self.id}:{self.session_fingerprint}"


@login_manager.user_loader
def load_user(session_id: str):
    """Re-checked on every request: deactivation or a password change ends existing sessions."""
    user_id, _, fingerprint = session_id.partition(":")
    user = db.session.get(User, int(user_id)) if user_id.isdigit() else None
    if user is None or not user.active or not hmac.compare_digest(fingerprint, user.session_fingerprint):
        return None
    return user
