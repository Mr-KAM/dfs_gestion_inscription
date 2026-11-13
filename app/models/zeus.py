"""Zeus mode: life of the training cohorts (classes, courses, scheduled sessions = calendar, roll calls).
Students are identified by their enrollment (DFS matricule), never by the SAS tables."""
from app.extensions import db
from app.utils import as_utc, utcnow

METHOD_SCAN, METHOD_MANUAL = "scan", "manual"


class ZeusClass(db.Model):
    """Created by the admin or a coach technique of the cohort; students join on invitation."""
    __tablename__ = "zeus_classes"

    id = db.Column(db.Integer, primary_key=True)
    cohort_id = db.Column(db.Integer, db.ForeignKey("cohorts.id", ondelete="CASCADE"), nullable=False, index=True)
    name = db.Column(db.String(80), nullable=False)
    coach_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"))
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)

    cohort = db.relationship("Cohort")
    coach = db.relationship("User")
    members = db.relationship("ZeusClassMember", back_populates="klass", cascade="all, delete-orphan",
                              order_by="ZeusClassMember.id")

    __table_args__ = (db.UniqueConstraint("cohort_id", "name", name="uq_zeus_classes_cohort_name"),)

    @property
    def joined(self) -> list:
        return [m for m in self.members if m.joined_at]


class ZeusClassMember(db.Model):
    """An invitation (joined_at empty) or a member. Declining deletes the row."""
    __tablename__ = "zeus_class_members"

    id = db.Column(db.Integer, primary_key=True)
    class_id = db.Column(db.Integer, db.ForeignKey("zeus_classes.id", ondelete="CASCADE"), nullable=False, index=True)
    enrollment_id = db.Column(db.Integer, db.ForeignKey("cohort_enrollments.id", ondelete="CASCADE"), nullable=False,
                              index=True)
    invited_by = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"))
    invited_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    joined_at = db.Column(db.DateTime(timezone=True))

    klass = db.relationship("ZeusClass", back_populates="members")
    enrollment = db.relationship("Enrollment")

    __table_args__ = (db.UniqueConstraint("class_id", "enrollment_id", name="uq_zeus_class_members_class_enrollment"),)


class ZeusCourse(db.Model):
    """A course of the cohort's programme, with its planned volume of hours."""
    __tablename__ = "zeus_courses"

    id = db.Column(db.Integer, primary_key=True)
    cohort_id = db.Column(db.Integer, db.ForeignKey("cohorts.id", ondelete="CASCADE"), nullable=False, index=True)
    name = db.Column(db.String(150), nullable=False)
    description = db.Column(db.String(255), default="")
    hours = db.Column(db.Integer, nullable=False, default=0)
    coach_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"))

    cohort = db.relationship("Cohort")
    coach = db.relationship("User")
    sessions = db.relationship("ZeusSession", back_populates="course", cascade="all, delete-orphan",
                               order_by="ZeusSession.starts_at")

    __table_args__ = (db.UniqueConstraint("cohort_id", "name", name="uq_zeus_courses_cohort_name"),)

    @property
    def scheduled_hours(self) -> float:
        return sum(s.hours for s in self.sessions)


class ZeusSession(db.Model):
    """A scheduled session of a course (the calendar), for one class or the whole cohort (class_id empty)."""
    __tablename__ = "zeus_sessions"

    id = db.Column(db.Integer, primary_key=True)
    course_id = db.Column(db.Integer, db.ForeignKey("zeus_courses.id", ondelete="CASCADE"), nullable=False, index=True)
    class_id = db.Column(db.Integer, db.ForeignKey("zeus_classes.id", ondelete="CASCADE"), index=True)
    starts_at = db.Column(db.DateTime(timezone=True), nullable=False, index=True)
    ends_at = db.Column(db.DateTime(timezone=True), nullable=False)
    room = db.Column(db.String(100), default="")
    coach_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"))
    created_by = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"))

    course = db.relationship("ZeusCourse", back_populates="sessions")
    klass = db.relationship("ZeusClass")
    coach = db.relationship("User", foreign_keys=[coach_id])
    marks = db.relationship("ZeusAttendance", back_populates="session", cascade="all, delete-orphan")

    __table_args__ = (db.CheckConstraint("ends_at > starts_at", name="ends_after_start"),)

    @property
    def hours(self) -> float:
        return round((as_utc(self.ends_at) - as_utc(self.starts_at)).total_seconds() / 3600, 2)

    @property
    def is_past(self) -> bool:
        return as_utc(self.ends_at) <= utcnow()

    @property
    def expected(self) -> list:
        """Enrollments expected at the session: the class members, or every registered student of the cohort."""
        if self.klass:
            return [m.enrollment for m in self.klass.joined]
        return [e for e in self.course.cohort.enrollments if e.matricule]


class ZeusNotification(db.Model):
    """Message to a whole cohort, one of its classes (class_id) or one student (enrollment_id)."""
    __tablename__ = "zeus_notifications"

    id = db.Column(db.Integer, primary_key=True)
    cohort_id = db.Column(db.Integer, db.ForeignKey("cohorts.id", ondelete="CASCADE"), nullable=False, index=True)
    class_id = db.Column(db.Integer, db.ForeignKey("zeus_classes.id", ondelete="CASCADE"), index=True)
    enrollment_id = db.Column(db.Integer, db.ForeignKey("cohort_enrollments.id", ondelete="CASCADE"), index=True)
    title = db.Column(db.String(150), nullable=False)
    body = db.Column(db.Text, nullable=False, default="")
    created_by = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"))
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow, index=True)

    cohort = db.relationship("Cohort")
    klass = db.relationship("ZeusClass")
    enrollment = db.relationship("Enrollment")
    author = db.relationship("User")

    @property
    def target_label(self) -> str:
        if self.enrollment:
            return self.enrollment.candidate.full_name
        return f"Classe {self.klass.name}" if self.klass else f"Cohorte {self.cohort.name}"


class ZeusAttendance(db.Model):
    """Present at a session (absent = no row once the session is over)."""
    __tablename__ = "zeus_attendances"

    id = db.Column(db.Integer, primary_key=True)
    session_id = db.Column(db.Integer, db.ForeignKey("zeus_sessions.id", ondelete="CASCADE"), nullable=False,
                           index=True)
    enrollment_id = db.Column(db.Integer, db.ForeignKey("cohort_enrollments.id", ondelete="CASCADE"), nullable=False,
                              index=True)
    method = db.Column(db.String(10), nullable=False, default=METHOD_MANUAL)
    marked_by = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"))
    marked_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)

    session = db.relationship("ZeusSession", back_populates="marks")
    enrollment = db.relationship("Enrollment")

    __table_args__ = (
        db.UniqueConstraint("session_id", "enrollment_id", name="uq_zeus_attendances_session_enrollment"),
    )
