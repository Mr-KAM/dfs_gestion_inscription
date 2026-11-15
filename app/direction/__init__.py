"""Activities calendar: the direction (DG, DGA) and the admin plan them and say who is concerned; every account
sees the activities that concern it (here, and in the Zeus calendar)."""
from datetime import timedelta

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.extensions import db
from app.models import Activity
from app.services import candidate_service, direction_service, zeus_service
from app.services.candidate_service import BusinessError

bp = Blueprint("direction", __name__, url_prefix="/activites")


@bp.route("/", methods=["GET", "POST"])
@login_required
def activities():
    planner = direction_service.can_plan(current_user)
    if request.method == "POST":
        if not planner:
            abort(403)
        try:
            a = direction_service.create(request.form, current_user)
            flash(f"Activité ajoutée au calendrier : {a.title}.", "success")
            return redirect(url_for("direction.activities", week=request.form.get("starts_at", "")[:10]))
        except BusinessError as exc:
            db.session.rollback()
            flash(str(exc), "danger")
    start = zeus_service.week_start(request.args.get("week"))
    days = [start + timedelta(days=i) for i in range(7)]
    roles, people = direction_service.audience_choices() if planner else ({}, [])
    return render_template("direction/activities.html", days=days, planner=planner, roles=roles, people=people,
                           activities=direction_service.activities_for(current_user, start, start + timedelta(days=7)),
                           audience=direction_service.audience_label, today=candidate_service.today().isoformat(),
                           prev=(start - timedelta(days=7)).date().isoformat(),
                           next=(start + timedelta(days=7)).date().isoformat())


@bp.post("/<int:activity_id>/supprimer")
@login_required
def delete(activity_id: int):
    if not direction_service.can_plan(current_user):
        abort(403)
    a = db.get_or_404(Activity, activity_id)
    title = a.title
    direction_service.delete(a)
    flash(f"Activité supprimée : {title}.", "success")
    return redirect(url_for("direction.activities"))
