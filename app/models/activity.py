"""Activities of the calendar planned by the direction (DG, DGA) or the admin, for the people concerned."""
from app.extensions import db
from app.utils import utcnow

activity_participants = db.Table(
    "activity_participants",
    db.Column("activity_id", db.Integer, db.ForeignKey("activities.id", ondelete="CASCADE"), primary_key=True),
    db.Column("user_id", db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
)


class Activity(db.Model):
    """Concerned: everyone, or the accounts of the chosen roles, or the chosen persons."""
    __tablename__ = "activities"

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(150), nullable=False)
    description = db.Column(db.Text, nullable=False, default="")
    place = db.Column(db.String(150), nullable=False, default="")
    starts_at = db.Column(db.DateTime(timezone=True), nullable=False, index=True)
    ends_at = db.Column(db.DateTime(timezone=True), nullable=False)
    everyone = db.Column(db.Boolean, nullable=False, default=False, server_default=db.false())
    roles = db.Column(db.JSON, nullable=False, default=list, server_default="[]")
    created_by = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"))
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)

    participants = db.relationship("User", secondary=activity_participants, order_by="User.full_name")
    author = db.relationship("User", foreign_keys=[created_by])

    __table_args__ = (db.CheckConstraint("ends_at > starts_at", name="ends_after_start"),)

    def concerns(self, user) -> bool:
        return self.everyone or user.role in (self.roles or []) or user in self.participants
