"""Infrastructure and inventory: buildings > levels > spaces, equipment (move, assign), consumables (stock)."""
import secrets
from datetime import date

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import AuditLog, Building, Consumable, ConsumableMovement, Equipment, Level, Space, User
from app.models.infra import (CONSUMABLE_CATEGORIES, CONSUMABLE_UNITS, EQUIPMENT_CATEGORIES, EQUIPMENT_STATUSES,
                              SPACE_USAGES)
from app.models.user import ROLE_CANDIDATE
from app.services import audit_service
from app.services.candidate_service import BusinessError


def _text(form, key: str, size: int) -> str:
    return " ".join(form.get(key, "").split())[:size]


def _int(form, key: str, minimum: int | None = None) -> int | None:
    value = form.get(key, "").strip()
    if not value:
        return None
    try:
        number = int(value)
    except ValueError:
        raise BusinessError(f"Nombre invalide : {value}")
    if minimum is not None and number < minimum:
        raise BusinessError(f"Le nombre doit être au moins {minimum}.")
    return number


def _commit(entity, action: str, details: dict, duplicate: str):
    db.session.add(entity)
    try:
        db.session.flush()
    except IntegrityError:
        db.session.rollback()
        raise BusinessError(duplicate)
    audit_service.log(action, entity, details)
    db.session.commit()
    return entity


# ---- buildings / levels / spaces

def save_building(form, building: Building | None = None) -> Building:
    building = building or Building()
    building.name, building.description = _text(form, "name", 100), _text(form, "description", 255)
    if not building.name:
        raise BusinessError("Le nom du bâtiment est obligatoire.")
    return _commit(building, "infra_update", {"building": building.name}, "Un bâtiment porte déjà ce nom.")


def add_level(building: Building, form) -> Level:
    number = _int(form, "number")
    if number is None:
        raise BusinessError("Indiquez le numéro du niveau (0 = rez-de-chaussée, 1 = 1er étage…).")
    level = Level(building=building, number=number, name=_text(form, "name", 100))
    return _commit(level, "infra_update", {"building": building.name, "level": number},
                   f"Le niveau {number} existe déjà dans {building.name}.")


def save_space(form, level: Level | None = None, space: Space | None = None) -> Space:
    space = space or Space(level=level)
    space.name, space.description = _text(form, "name", 100), _text(form, "description", 255)
    space.usage = form.get("usage") if form.get("usage") in SPACE_USAGES else "Autre"
    space.capacity = _int(form, "capacity", minimum=0)
    if not space.name:
        raise BusinessError("Le nom de la salle est obligatoire.")
    return _commit(space, "infra_update", {"space": space.name, "usage": space.usage},
                   "Une salle porte déjà ce nom à ce niveau.")


def delete(entity) -> None:
    """Only empty places can be deleted: nothing is lost by mistake."""
    if isinstance(entity, Building) and entity.levels or isinstance(entity, Level) and entity.spaces:
        raise BusinessError("Supprimez d'abord ce qu'il contient.")
    if isinstance(entity, Space) and (entity.equipment or db.session.scalar(
            select(Consumable.id).where(Consumable.space_id == entity.id).limit(1))):
        raise BusinessError("Cette salle contient du matériel ou des consommables : déplacez-les d'abord.")
    audit_service.log("infra_update", entity, {"deleted": getattr(entity, "name", "") or getattr(entity, "number", "")})
    db.session.delete(entity)
    db.session.commit()


def space_choices() -> list[tuple[int, str]]:
    """Every space as (id, 'Bâtiment › N0 › Salle'), for the location selects."""
    rows = db.session.execute(select(Space.id, Building.name, Level.number, Space.name).join(Level, Space.level)
                              .join(Building, Level.building).order_by(Building.name, Level.number, Space.name))
    return [(space_id, f"{building} › N{number} › {name}") for space_id, building, number, name in rows]


def staff_choices() -> list[User]:
    return db.session.scalars(select(User).where(User.role != ROLE_CANDIDATE, User.active.is_(True))
                              .order_by(User.full_name, User.email)).all()


def buildings() -> list[Building]:
    return db.session.scalars(select(Building).order_by(Building.name)).all()


# ---- equipment

def _space(form) -> Space | None:
    space_id = _int(form, "space_id")
    return db.session.get(Space, space_id) if space_id else None


def _holder(form) -> User | None:
    user_id = _int(form, "assigned_to")
    user = db.session.get(User, user_id) if user_id else None
    if user is not None and user.is_candidate:
        raise BusinessError("Le matériel s'attribue à un membre du personnel.")
    return user


def _label(space: Space | None) -> str:
    return space.full_label if space else "—"


def save_equipment(form, item: Equipment | None = None) -> Equipment:
    """Create or edit; a change of place, holder or state is written to the item's history (audit log)."""
    creating = item is None
    item = item or Equipment(code="")
    before = {"space": _label(item.space), "holder": item.holder.display_name if item.holder else "—",
              "status": EQUIPMENT_STATUSES.get(item.status, ("", "—"))[1]}
    item.name, item.brand = _text(form, "name", 150), _text(form, "brand", 100)
    item.serial_number, item.notes = _text(form, "serial_number", 100), form.get("notes", "").strip()[:5000]
    item.category = form.get("category") if form.get("category") in EQUIPMENT_CATEGORIES else "Autre"
    item.status = form.get("status") if form.get("status") in EQUIPMENT_STATUSES else "in_service"
    item.space, item.holder = _space(form), _holder(form)
    acquired = form.get("acquired_on", "").strip()
    try:
        item.acquired_on = date.fromisoformat(acquired) if acquired else None
    except ValueError:
        raise BusinessError("Date d'acquisition invalide.")
    code = _text(form, "code", 30).upper()
    if not item.name:
        raise BusinessError("Le nom du matériel est obligatoire.")
    item.code = code or item.code or f"TMP-{secrets.token_hex(8)}"  # replaced by INV-00042 once the id is known
    db.session.add(item)
    try:
        db.session.flush()
    except IntegrityError:
        db.session.rollback()
        raise BusinessError("Ce numéro d'inventaire est déjà utilisé.")
    if item.code.startswith("TMP-"):
        item.code = f"INV-{item.id:05d}"
    after = {"space": _label(item.space), "holder": item.holder.display_name if item.holder else "—",
             "status": item.status_badge[1]}
    changes = {k: f"{before[k]} → {after[k]}" for k in after if not creating and before[k] != after[k]}
    audit_service.log("equipment_create" if creating else "equipment_update", item,
                      {"code": item.code, "name": item.name, **(after if creating else changes)})
    db.session.commit()
    return item


def equipment_history(item: Equipment) -> list[AuditLog]:
    return db.session.scalars(select(AuditLog).where(AuditLog.entity_type == "Equipment",
                                                     AuditLog.entity_id == str(item.id))
                              .order_by(AuditLog.created_at.desc())).all()


def equipment_query(args):
    stmt = select(Equipment).order_by(Equipment.code)
    if q := args.get("q", "").strip():
        like = f"%{q}%"
        stmt = stmt.where(or_(Equipment.code.ilike(like), Equipment.name.ilike(like), Equipment.brand.ilike(like),
                              Equipment.serial_number.ilike(like)))
    if args.get("category"):
        stmt = stmt.where(Equipment.category == args["category"])
    if args.get("status"):
        stmt = stmt.where(Equipment.status == args["status"])
    if args.get("space") == "none":
        stmt = stmt.where(Equipment.space_id.is_(None))
    elif args.get("space", "").isdigit():
        stmt = stmt.where(Equipment.space_id == int(args["space"]))
    if args.get("holder") == "none":
        stmt = stmt.where(Equipment.assigned_to.is_(None))
    elif args.get("holder", "").isdigit():
        stmt = stmt.where(Equipment.assigned_to == int(args["holder"]))
    return stmt


def equipment_rows(items: list[Equipment]) -> list[dict]:
    """Inventory export."""
    return [{"N° inventaire": e.code, "Désignation": e.name, "Catégorie": e.category, "Marque / modèle": e.brand,
             "N° de série": e.serial_number, "État": e.status_badge[1], "Emplacement": _label(e.space),
             "Usage de la salle": e.space.usage if e.space else "",
             "Attribué à": e.holder.display_name if e.holder else "",
             "Acquis le": e.acquired_on.strftime("%d/%m/%Y") if e.acquired_on else "", "Notes": e.notes or ""}
            for e in items]


def stats() -> dict:
    by_status = dict(db.session.execute(select(Equipment.status, func.count()).group_by(Equipment.status)).all())
    low = db.session.scalars(select(Consumable).where(Consumable.quantity <= Consumable.min_quantity)
                             .order_by(Consumable.name)).all()
    return {"equipment": sum(by_status.values()), "by_status": by_status, "low": low,
            "spaces": db.session.scalar(select(func.count(Space.id)))}


# ---- consumables

def save_consumable(form, item: Consumable | None = None) -> Consumable:
    creating = item is None
    item = item or Consumable(quantity=0)
    item.name = _text(form, "name", 150)
    item.category = form.get("category") if form.get("category") in CONSUMABLE_CATEGORIES else "Autre"
    item.unit = form.get("unit") if form.get("unit") in CONSUMABLE_UNITS else "pièce"
    item.min_quantity = _int(form, "min_quantity", minimum=0) or 0
    item.space = _space(form)
    if not item.name:
        raise BusinessError("Le nom du consommable est obligatoire.")
    item = _commit(item, "consumable_update", {"name": item.name}, "Un consommable porte déjà ce nom.")
    if creating and (initial := _int(form, "quantity", minimum=0)):
        move_stock(item, initial, "Stock initial", None)
    return item


def move_stock(item: Consumable, delta: int, note: str, user) -> ConsumableMovement:
    """+ entry (delivery, purchase) / - exit (distribution). The stock never goes below zero."""
    if not delta:
        raise BusinessError("Indiquez une quantité.")
    if item.quantity + delta < 0:
        raise BusinessError(f"Stock insuffisant : il reste {item.quantity} {item.unit}(s) de {item.name}.")
    item.quantity += delta
    movement = ConsumableMovement(consumable=item, delta=delta, balance=item.quantity, note=note.strip()[:255],
                                  created_by=getattr(user, "id", None))
    db.session.add(movement)
    audit_service.log("consumable_move", item, {"delta": delta, "balance": item.quantity, "note": movement.note})
    db.session.commit()
    return movement
