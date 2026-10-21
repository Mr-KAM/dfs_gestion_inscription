from app.extensions import db
from app.utils import as_utc, utcnow

METHOD_SELF = "self_qr"      # the student scanned the QR code shown by the admin
METHOD_SCAN = "admin_scan"   # the admin scanned the student's matricule QR code
METHOD_ADMIN = "admin"       # ticked by hand by the admin
HOURS_CHOICES = (1, 2, 3, 4, 7)  # hours counted by a roll call (= absence hours when not marked in time)
METHOD_LABELS = {METHOD_SELF: "QR scanné par l'étudiant", METHOD_SCAN: "Scanné par l'admin", METHOD_ADMIN: "Saisi par l'admin"}


class AttendanceSession(db.Model):
    """A check-in period for a SAS class ('' = the whole SAS). Absent = no Attendance row once closed."""
    __tablename__ = "attendance_sessions"

    id = db.Column(db.Integer, primary_key=True)
    sas_id = db.Column(db.Integer, db.ForeignKey("sas.id", ondelete="CASCADE"), nullable=False, index=True)
    class_name = db.Column(db.String(50), nullable=False, default="")
    label = db.Column(db.String(100), nullable=False)
    opened_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)  # chosen date and time
    closes_at = db.Column(db.DateTime(timezone=True), nullable=False)  # end of the marking window
    hours = db.Column(db.Integer, nullable=False, default=1, server_default="1")
    created_by = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"))

    sas = db.relationship("Sas", back_populates="attendance_sessions")
    marks = db.relationship("Attendance", back_populates="session", cascade="all, delete-orphan")

    @property
    def is_open(self) -> bool:
        return as_utc(self.opened_at) <= utcnow() < as_utc(self.closes_at)

    @property
    def is_closed(self) -> bool:
        """Time is up: whoever has not marked is absent for `hours` hours."""
        return utcnow() >= as_utc(self.closes_at)

    @property
    def state_badge(self) -> tuple[str, str]:
        if self.is_closed:
            return "secondary", "Clôturé"
        return ("warning", "En cours") if self.is_open else ("info", "Prévu")


class Attendance(db.Model):
    __tablename__ = "attendances"

    id = db.Column(db.Integer, primary_key=True)
    session_id = db.Column(db.Integer, db.ForeignKey("attendance_sessions.id", ondelete="CASCADE"), nullable=False,
                           index=True)
    candidate_id = db.Column(db.Integer, db.ForeignKey("candidates.id", ondelete="CASCADE"), nullable=False,
                             index=True)
    marked_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    method = db.Column(db.String(20), nullable=False)
    marked_by = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"))

    session = db.relationship("AttendanceSession", back_populates="marks")
    candidate = db.relationship("Candidate")

    __table_args__ = (db.UniqueConstraint("session_id", "candidate_id", name="uq_attendances_session_candidate"),)
