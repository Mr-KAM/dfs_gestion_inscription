"""Infrastructure and inventory (responsable de cité): buildings > levels > spaces, equipment, consumables."""
from app.extensions import db
from app.utils import utcnow

SPACE_USAGES = ["Chambre", "Salle de classe", "Bureau", "Salle de réunion", "Salle informatique", "Magasin / stock",
                "Cuisine / réfectoire", "Sanitaires", "Hall / accueil", "Autre"]
EQUIPMENT_CATEGORIES = ["Informatique", "Réseau", "Audiovisuel", "Mobilier", "Literie", "Électroménager",
                        "Outillage", "Autre"]
EQUIPMENT_STATUSES = {"in_service": ("success", "En service"), "in_stock": ("info", "En stock"),
                      "broken": ("danger", "En panne"), "repair": ("warning", "En réparation"),
                      "retired": ("secondary", "Hors service")}
CONSUMABLE_CATEGORIES = ["Fournitures de bureau", "Eau", "Café / thé", "Entretien", "Hygiène", "Alimentation",
                         "Informatique", "Autre"]
CONSUMABLE_UNITS = ["pièce", "paquet", "carton", "rame", "bouteille", "pack", "litre", "kg", "boîte"]


class Building(db.Model):
    __tablename__ = "buildings"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False, unique=True)
    description = db.Column(db.String(255), default="")
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)

    levels = db.relationship("Level", back_populates="building", order_by="Level.number", cascade="all, delete-orphan")


class Level(db.Model):
    """Floor: 0 = ground floor (rez-de-chaussée), 1 = first floor, -1 = basement..."""
    __tablename__ = "levels"

    id = db.Column(db.Integer, primary_key=True)
    building_id = db.Column(db.Integer, db.ForeignKey("buildings.id", ondelete="CASCADE"), nullable=False, index=True)
    number = db.Column(db.Integer, nullable=False)
    name = db.Column(db.String(100), default="")

    building = db.relationship("Building", back_populates="levels")
    spaces = db.relationship("Space", back_populates="level", order_by="Space.name", cascade="all, delete-orphan")

    __table_args__ = (db.UniqueConstraint("building_id", "number", name="uq_levels_building_number"),)

    @property
    def label(self) -> str:
        name = self.name or ("Rez-de-chaussée" if self.number == 0 else "")
        return f"Niveau {self.number}" + (f" — {name}" if name else "")


class Space(db.Model):
    """A room of a level: bedroom, classroom, office..."""
    __tablename__ = "spaces"

    id = db.Column(db.Integer, primary_key=True)
    level_id = db.Column(db.Integer, db.ForeignKey("levels.id", ondelete="CASCADE"), nullable=False, index=True)
    name = db.Column(db.String(100), nullable=False)
    usage = db.Column(db.String(40), nullable=False, default="Autre")
    capacity = db.Column(db.Integer)  # places / beds, optional
    description = db.Column(db.String(255), default="")

    level = db.relationship("Level", back_populates="spaces")
    equipment = db.relationship("Equipment", back_populates="space", order_by="Equipment.name")

    __table_args__ = (db.UniqueConstraint("level_id", "name", name="uq_spaces_level_name"),)

    @property
    def full_label(self) -> str:
        return f"{self.level.building.name} › N{self.level.number} › {self.name}"


class Equipment(db.Model):
    """One inventoried item: where it is, who it is lent to, its state."""
    __tablename__ = "equipment"

    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(30), nullable=False, unique=True)  # inventory number, INV-00001 by default
    name = db.Column(db.String(150), nullable=False)
    category = db.Column(db.String(40), nullable=False, default="Autre")
    brand = db.Column(db.String(100), default="")  # brand / model
    serial_number = db.Column(db.String(100), default="")
    status = db.Column(db.String(20), nullable=False, default="in_service")
    space_id = db.Column(db.Integer, db.ForeignKey("spaces.id", ondelete="SET NULL"), index=True)
    assigned_to = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), index=True)
    acquired_on = db.Column(db.Date)
    notes = db.Column(db.Text, default="")
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)

    space = db.relationship("Space", back_populates="equipment")
    holder = db.relationship("User")

    @property
    def status_badge(self) -> tuple[str, str]:
        return EQUIPMENT_STATUSES.get(self.status, ("secondary", self.status))


class Consumable(db.Model):
    """Supplies managed by quantity (paper, water, coffee...); every change is a ConsumableMovement."""
    __tablename__ = "consumables"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(150), nullable=False, unique=True)
    category = db.Column(db.String(40), nullable=False, default="Autre")
    unit = db.Column(db.String(20), nullable=False, default="pièce")
    quantity = db.Column(db.Integer, nullable=False, default=0)
    min_quantity = db.Column(db.Integer, nullable=False, default=0)  # alert threshold
    space_id = db.Column(db.Integer, db.ForeignKey("spaces.id", ondelete="SET NULL"), index=True)  # storage place

    space = db.relationship("Space")
    movements = db.relationship("ConsumableMovement", back_populates="consumable", cascade="all, delete-orphan",
                                order_by="ConsumableMovement.id.desc()")  # id: same-millisecond safe

    __table_args__ = (db.CheckConstraint("quantity >= 0", name="quantity_positive"),)

    @property
    def is_low(self) -> bool:
        return self.quantity <= self.min_quantity


class ConsumableMovement(db.Model):
    __tablename__ = "consumable_movements"

    id = db.Column(db.Integer, primary_key=True)
    consumable_id = db.Column(db.Integer, db.ForeignKey("consumables.id", ondelete="CASCADE"), nullable=False,
                              index=True)
    delta = db.Column(db.Integer, nullable=False)  # + entry (purchase, delivery), - exit (distribution, use)
    balance = db.Column(db.Integer, nullable=False)  # quantity after the movement
    note = db.Column(db.String(255), default="")  # supplier, recipient, reason
    created_by = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"))
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)

    consumable = db.relationship("Consumable", back_populates="movements")
    author = db.relationship("User")
