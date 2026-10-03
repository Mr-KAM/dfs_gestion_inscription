from flask_login import UserMixin
from werkzeug.security import check_password_hash, generate_password_hash

from app.extensions import db, login_manager
from app.utils import utcnow

ROLE_ADMIN = "admin"
ROLE_MOTIVATION = "motivation_tester"
ROLE_TECHNICAL = "technical_tester"
ROLE_SUPERVISOR = "supervisor"
ROLES = {
    ROLE_ADMIN: "Administrateur",
    ROLE_SUPERVISOR: "Superviseur",
    ROLE_MOTIVATION: "Testeur motivation",
    ROLE_TECHNICAL: "Testeur technique",
}
# Who may do what. The supervisor combines both tester profiles, without any admin right.
MOTIVATION_ROLES = (ROLE_ADMIN, ROLE_SUPERVISOR, ROLE_MOTIVATION)
TECHNICAL_ROLES = (ROLE_ADMIN, ROLE_SUPERVISOR, ROLE_TECHNICAL)
ALL_ROLES = tuple(ROLES)


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

    __table_args__ = (
        db.CheckConstraint(
            f"role IN ('{ROLE_ADMIN}', '{ROLE_SUPERVISOR}', '{ROLE_MOTIVATION}', '{ROLE_TECHNICAL}')", name="role"
        ),
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
        return ROLES.get(self.role, self.role)

    @property
    def display_name(self) -> str:
        return self.full_name or self.email

    def has_role(self, *roles: str) -> bool:
        return self.role in roles


@login_manager.user_loader
def load_user(user_id: str):
    return db.session.get(User, int(user_id))
