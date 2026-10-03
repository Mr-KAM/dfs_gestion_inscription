from flask import Blueprint, render_template, request
from flask_login import current_user, login_required

from app.models.user import ROLE_ADMIN, ROLE_MOTIVATION, ROLE_SUPERVISOR
from app.services import candidate_service, timer_service

bp = Blueprint("dashboard", __name__)


@bp.get("/")
@login_required
def index():
    """One URL, role-specific dashboard. `?partial=1` returns the polled fragment."""
    partial = request.args.get("partial")
    if current_user.role == ROLE_ADMIN:
        ctx = {"stats": candidate_service.admin_stats(), "groups": candidate_service.group_summary()}
        return render_template("dashboard/_admin_stats.html" if partial else "dashboard/admin.html", **ctx)
    if current_user.role == ROLE_SUPERVISOR:
        day = candidate_service.today()
        ctx = {"day": day, "mstats": candidate_service.motivation_stats(day),
               "upcoming": candidate_service.next_candidates(day),
               "tstats": candidate_service.technical_stats(), "sessions": timer_service.active_sessions()}
        return render_template("dashboard/_supervisor.html" if partial else "dashboard/supervisor.html", **ctx)
    if current_user.role == ROLE_MOTIVATION:
        day = candidate_service.today()
        ctx = {"day": day, "stats": candidate_service.motivation_stats(day),
               "upcoming": candidate_service.next_candidates(day)}
        return render_template("dashboard/_motivation_stats.html" if partial else "dashboard/motivation.html", **ctx)
    ctx = {"stats": candidate_service.technical_stats(), "sessions": timer_service.active_sessions()}
    return render_template("dashboard/_technical_live.html" if partial else "dashboard/technical.html", **ctx)
