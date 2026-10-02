from app.extensions import db
from app.utils import utcnow


class Room(db.Model):
    __tablename__ = "rooms"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False)
    description = db.Column(db.String(255), default="")
    active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)

    workstations = db.relationship(
        "Workstation", back_populates="room", order_by="Workstation.computer_number", cascade="all, delete-orphan"
    )


class Workstation(db.Model):
    __tablename__ = "workstations"

    id = db.Column(db.Integer, primary_key=True)
    room_id = db.Column(db.Integer, db.ForeignKey("rooms.id", ondelete="CASCADE"), nullable=False, index=True)
    computer_number = db.Column(db.String(30), nullable=False)
    label = db.Column(db.String(100), default="")
    active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)

    room = db.relationship("Room", back_populates="workstations")

    __table_args__ = (db.UniqueConstraint("room_id", "computer_number", name="uq_workstation_room_number"),)

    @property
    def name(self) -> str:
        return self.label or self.computer_number
