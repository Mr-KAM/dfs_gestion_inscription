from app.extensions import db
from app.utils import as_utc, utcnow

STATUS_PENDING = "pending"
STATUS_RUNNING = "running"
STATUS_PAUSED = "paused"
STATUS_STOPPED = "stopped"
STATUS_COMPLETED = "completed"
ACTIVE_STATUSES = (STATUS_RUNNING, STATUS_PAUSED)
STATUS_BADGES = {
    STATUS_PENDING: ("info", "En attente"),
    STATUS_RUNNING: ("primary", "En cours"),
    STATUS_PAUSED: ("warning", "Pause"),
    STATUS_STOPPED: ("danger", "Arrêté"),
    STATUS_COMPLETED: ("success", "Terminé"),
}

_ACTIVE_SQL = "status IN ('running', 'paused')"


class TechnicalTestSession(db.Model):
    __tablename__ = "technical_test_sessions"

    id = db.Column(db.Integer, primary_key=True)
    candidate_id = db.Column(db.Integer, db.ForeignKey("candidates.id", ondelete="CASCADE"), nullable=False, index=True)
    technical_tester_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"))
    room_id = db.Column(db.Integer, db.ForeignKey("rooms.id"), nullable=False)
    workstation_id = db.Column(db.Integer, db.ForeignKey("workstations.id"), nullable=False, index=True)

    duration_seconds = db.Column(db.Integer, nullable=False)
    started_at = db.Column(db.DateTime(timezone=True))
    paused_at = db.Column(db.DateTime(timezone=True))
    total_pause_seconds = db.Column(db.Integer, nullable=False, default=0)
    finished_at = db.Column(db.DateTime(timezone=True))
    status = db.Column(db.String(20), nullable=False, default=STATUS_PENDING, index=True)

    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)

    candidate = db.relationship("Candidate", back_populates="sessions")
    tester = db.relationship("User")
    room = db.relationship("Room")
    workstation = db.relationship("Workstation")

    __table_args__ = (
        db.CheckConstraint(
            "status IN ('pending', 'running', 'paused', 'stopped', 'completed')", name="status"
        ),
        db.CheckConstraint("duration_seconds > 0", name="duration_positive"),
        # Rule 5: a workstation hosts at most one active session (DB-enforced).
        db.Index(
            "uq_active_session_workstation", "workstation_id", unique=True,
            postgresql_where=db.text(_ACTIVE_SQL), sqlite_where=db.text(_ACTIVE_SQL),
        ),
        # Rule 6: a candidate has at most one active session.
        db.Index(
            "uq_active_session_candidate", "candidate_id", unique=True,
            postgresql_where=db.text(_ACTIVE_SQL), sqlite_where=db.text(_ACTIVE_SQL),
        ),
    )

    @property
    def is_active(self) -> bool:
        return self.status in ACTIVE_STATUSES

    @property
    def technical_completed(self) -> bool:  # ponytail: derived, not a column
        return self.status == STATUS_COMPLETED

    @property
    def status_badge(self) -> tuple[str, str]:
        return STATUS_BADGES[self.status]

    def elapsed_seconds(self, now=None) -> int:
        """Effective test time, computed only from server timestamps."""
        if not self.started_at:
            return 0
        now = now or utcnow()
        end = as_utc(self.finished_at) or now
        if self.status == STATUS_PAUSED and self.paused_at:
            end = as_utc(self.paused_at)
        elapsed = (end - as_utc(self.started_at)).total_seconds() - (self.total_pause_seconds or 0)
        return max(0, int(elapsed))

    def remaining_seconds(self, now=None) -> int:
        return max(0, self.duration_seconds - self.elapsed_seconds(now))
