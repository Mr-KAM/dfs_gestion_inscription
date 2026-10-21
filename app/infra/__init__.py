"""Infrastructure and inventory, for the responsable de cité (and the admin):
buildings > levels > spaces (name + usage), equipment (inventory, moves, lent to staff), consumables (stock)."""
import io

import pandas as pd
from flask import Blueprint, flash, redirect, render_template, request, send_file, url_for
from flask_login import current_user
from sqlalchemy import select

from app.auth import _safe_next
from app.extensions import db
from app.models import Building, Consumable, Equipment, Level, Space
from app.models.infra import (CONSUMABLE_CATEGORIES, CONSUMABLE_UNITS, EQUIPMENT_CATEGORIES, EQUIPMENT_STATUSES,
                              SPACE_USAGES)
from app.models.user import ROLE_ADMIN, ROLE_RESIDENCE
from app.services import audit_service, infra_service
from app.services.candidate_service import BusinessError
from app.utils import roles_required

bp = Blueprint("infra", __name__, url_prefix="/infrastructures")


@bp.before_request
@roles_required(ROLE_ADMIN, ROLE_RESIDENCE)
def require_residence():
    """Every /infrastructures route: admin or responsable de cité (server-side)."""


def _try(action, success: str) -> bool:
    try:
        action()
        flash(success, "success")
        return True
    except BusinessError as exc:
        db.session.rollback()
        flash(str(exc), "danger")
        return False


# ------------------------------------------------------------------ buildings > levels > spaces

@bp.get("/")
def index():
    return render_template("infra/index.html", buildings=infra_service.buildings(), stats=infra_service.stats(),
                           usages=SPACE_USAGES, statuses=EQUIPMENT_STATUSES)


@bp.post("/buildings")
def building_save():
    building = db.session.get(Building, request.form.get("building_id", 0, type=int) or 0)
    _try(lambda: infra_service.save_building(request.form, building), "Bâtiment enregistré.")
    return redirect(url_for("infra.index"))


@bp.post("/buildings/<int:building_id>/levels")
def level_add(building_id: int):
    building = db.get_or_404(Building, building_id)
    _try(lambda: infra_service.add_level(building, request.form), "Niveau ajouté.")
    return redirect(url_for("infra.index") + f"#b{building.id}")


@bp.post("/levels/<int:level_id>/spaces")
def space_add(level_id: int):
    level = db.get_or_404(Level, level_id)
    _try(lambda: infra_service.save_space(request.form, level=level), "Salle ajoutée.")
    return redirect(url_for("infra.index") + f"#b{level.building_id}")


@bp.post("/<kind>/<int:entity_id>/delete")
def delete(kind: str, entity_id: int):
    model = {"buildings": Building, "levels": Level, "spaces": Space}.get(kind)
    entity = db.get_or_404(model, entity_id) if model else None
    if entity is None:
        return redirect(url_for("infra.index"))
    _try(lambda: infra_service.delete(entity), "Supprimé.")
    return redirect(url_for("infra.index"))


@bp.route("/spaces/<int:space_id>", methods=["GET", "POST"])
def space(space_id: int):
    """Room inventory: its equipment and stored consumables; edit the room."""
    space = db.get_or_404(Space, space_id)
    if request.method == "POST" and _try(lambda: infra_service.save_space(request.form, space=space),
                                         "Salle mise à jour."):
        return redirect(url_for("infra.space", space_id=space.id))
    consumables = db.session.scalars(select(Consumable).where(Consumable.space_id == space.id)
                                     .order_by(Consumable.name)).all()
    return render_template("infra/space.html", space=space, consumables=consumables, usages=SPACE_USAGES,
                           **_equipment_form_context())


# ------------------------------------------------------------------ equipment

def _equipment_form_context() -> dict:
    return {"categories": EQUIPMENT_CATEGORIES, "statuses": EQUIPMENT_STATUSES,
            "spaces": infra_service.space_choices(), "staff": infra_service.staff_choices()}


@bp.route("/equipment", methods=["GET", "POST"])
def equipment_list():
    if request.method == "POST":
        created = []
        if _try(lambda: created.append(infra_service.save_equipment(request.form)), "Matériel ajouté."):
            back = request.form.get("back")
            return redirect(_safe_next(back) if back else url_for("infra.equipment", item_id=created[0].id))
        if request.form.get("back"):
            return redirect(_safe_next(request.form["back"]))
    items = db.session.scalars(infra_service.equipment_query(request.args).limit(1000)).all()
    filters = {k: request.args.get(k, "") for k in ("q", "category", "status", "space", "holder")}
    return render_template("infra/equipment_list.html", items=items, filters=filters, **_equipment_form_context())


@bp.route("/equipment/<int:item_id>", methods=["GET", "POST"])
def equipment(item_id: int):
    item = db.get_or_404(Equipment, item_id)
    if request.method == "POST" and _try(lambda: infra_service.save_equipment(request.form, item),
                                         "Matériel mis à jour."):
        return redirect(url_for("infra.equipment", item_id=item.id))
    return render_template("infra/equipment.html", item=item, history=infra_service.equipment_history(item),
                           **_equipment_form_context())


@bp.get("/equipment/export")
def equipment_export():
    """Inventory (current filters) in Excel or CSV."""
    items = db.session.scalars(infra_service.equipment_query(request.args)).all()
    df = pd.DataFrame(infra_service.equipment_rows(items))
    fmt = "xlsx" if request.args.get("format") == "xlsx" else "csv"
    audit_service.log("export", details={"kind": "inventory", "format": fmt, "rows": len(items)})
    db.session.commit()
    buffer = io.BytesIO()
    if fmt == "xlsx":
        df.to_excel(buffer, index=False, sheet_name="Inventaire")
        mimetype = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    else:
        buffer.write(df.to_csv(index=False, sep=";").encode("utf-8-sig"))
        mimetype = "text/csv"
    buffer.seek(0)
    return send_file(buffer, mimetype=mimetype, as_attachment=True, download_name=f"inventaire_materiel.{fmt}")


# ------------------------------------------------------------------ consumables

@bp.route("/consumables", methods=["GET", "POST"])
def consumables():
    if request.method == "POST" and _try(lambda: infra_service.save_consumable(request.form), "Consommable ajouté."):
        return redirect(url_for("infra.consumables"))
    category = request.args.get("category", "")
    stmt = select(Consumable).order_by(Consumable.category, Consumable.name)
    items = db.session.scalars(stmt.where(Consumable.category == category) if category else stmt).all()
    return render_template("infra/consumables.html", items=items, category=category,
                           categories=CONSUMABLE_CATEGORIES, units=CONSUMABLE_UNITS,
                           spaces=infra_service.space_choices())


@bp.post("/consumables/<int:item_id>/move")
def consumable_move(item_id: int):
    """Entry (+) or exit (-) of stock, with a note (supplier, recipient...)."""
    item = db.get_or_404(Consumable, item_id)
    quantity = request.form.get("quantity", 0, type=int) or 0
    delta = quantity if request.form.get("direction") == "in" else -quantity
    _try(lambda: infra_service.move_stock(item, delta, request.form.get("note", ""), current_user),
         f"{item.name} : {'+' if delta > 0 else ''}{delta} {item.unit}(s).")
    return redirect(_safe_next(request.form["back"]) if request.form.get("back") else url_for("infra.consumables"))


@bp.route("/consumables/<int:item_id>", methods=["GET", "POST"])
def consumable(item_id: int):
    item = db.get_or_404(Consumable, item_id)
    if request.method == "POST" and _try(lambda: infra_service.save_consumable(request.form, item),
                                         "Consommable mis à jour."):
        return redirect(url_for("infra.consumable", item_id=item.id))
    return render_template("infra/consumable.html", item=item, categories=CONSUMABLE_CATEGORIES, units=CONSUMABLE_UNITS,
                           spaces=infra_service.space_choices())
