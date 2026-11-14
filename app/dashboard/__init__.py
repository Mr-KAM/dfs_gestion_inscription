from flask import Blueprint, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required

from app.models.user import (CAMPUS_ROLES, JURY_ONLY_ROLES, ROLE_ADMIN, ROLE_MOTIVATION, ROLE_RESIDENCE,
                             ROLE_SUPERVISOR)
from app.services import candidate_service, sas_service, site_service, timer_service, zeus_service

bp = Blueprint("dashboard", __name__)


@bp.get("/")
@login_required
def index():
    """One URL, role-specific dashboard. `?partial=1` returns the polled fragment."""
    # Zeus mode: its users come back to Zeus here; the admin only right after login (on the Zeus domain even with
    # the mode off: he prepares it). ?classique=1, Zeus's "Classique" button: always the classic side, and it also
    # drops a landing flag left by a login that went straight to a Zeus page.
    landing = session.pop("zeus_landing", False)
    if request.args.get("classique") != "1" and (
            zeus_service.stays_in_zeus(current_user)
            or (landing and (zeus_service.lands_in_zeus(current_user) or (
                current_user.role == ROLE_ADMIN and site_service.current_site() == site_service.ZEUS)))):
        return redirect(url_for("zeus.home"))
    if current_user.is_candidate:
        return redirect(url_for("portal.home"))
    if current_user.role in JURY_ONLY_ROLES:
        return redirect(url_for("jury.index"))
    if current_user.role == ROLE_RESIDENCE:
        return redirect(url_for("infra.index"))
    if current_user.role in CAMPUS_ROLES and current_user.role != ROLE_ADMIN:
        return redirect(url_for("campus.index"))
    partial = request.args.get("partial")
    if current_user.role == ROLE_ADMIN:
        ctx = {"stats": candidate_service.admin_stats(), "groups": candidate_service.group_summary()}
        if not partial:  # SAS figures stay out of the 10 s polling
            ctx["sas_stats"] = sas_service.dashboard_stats()
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
