from app.extensions import db
from app.utils import utcnow


class Sas(db.Model):
    """Next recruitment stage: a cohort of admitted candidates (bedroom + equipment are on the candidate)."""
    __tablename__ = "sas"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False)
    description = db.Column(db.String(255), default="")
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)

    members = db.relationship("Candidate", back_populates="sas", order_by="Candidate.full_name")
    attendance_sessions = db.relationship("AttendanceSession", back_populates="sas", cascade="all, delete-orphan",
                                          order_by="AttendanceSession.opened_at.desc()")
