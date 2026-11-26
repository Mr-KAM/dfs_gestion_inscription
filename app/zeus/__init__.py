"""Zeus mode: the training-cohort interface (own layout and theme), switched on by the admin.
Admin, reception, residence, the coaches of a cohort and the registered students land here at login."""
from datetime import timedelta

from flask import Blueprint, abort, flash, jsonify, make_response, redirect, render_template, request, url_for
from flask import session as cookie_session  # `session` is the session (séance) view below
from flask_login import current_user, login_required

from app.auth import _safe_next
from app.extensions import db
from app.models import (Cohort, Enrollment, User, ZeusBirthdayWish, ZeusClass, ZeusClassCoach, ZeusClassMember,
                        ZeusClassRole, ZeusCourse, ZeusNotification, ZeusSession)
from app.models.user import ROLE_ADMIN, ROLE_TRADE_COACH
from app.services import (candidate_service, zeus_birthday_service, zeus_calendar_service, zeus_message_service,
                          zeus_service)
from app.services.candidate_service import BusinessError
from app.utils import allow_camera

bp = Blueprint("zeus", __name__, url_prefix="/zeus")


@bp.before_request
@login_required
def zeus_users_only():
    if not zeus_service.can_enter(current_user):
        abort(403)
    if not zeus_service.enabled() and current_user.role != ROLE_ADMIN:  # the admin prepares it before opening it
        flash("Le mode Zeus n'est pas activé.", "info")
        return redirect(url_for("dashboard.index"))
    if current_user.is_candidate and current_user.must_change_password:
        return redirect(url_for("portal.password"))
    return None


def _cohort(cohort_id: int) -> Cohort:
    cohort = db.get_or_404(Cohort, cohort_id)
    if not zeus_service.can_see(current_user, cohort):
        abort(404)
    return cohort


def _require_manage(cohort: Cohort) -> None:
    if not zeus_service.can_manage(current_user, cohort):
        abort(403)


def _back(default: str):
    return redirect(request.form.get("next") if (request.form.get("next") or "").startswith("/zeus") else default)


# ---- home

@bp.get("/")
def home():
    e = zeus_service.student_enrollment(current_user)
    week = zeus_service.week_start(None)
    splash = not cookie_session.get("zeus_splash")  # loading animation: once per session
    cookie_session["zeus_splash"] = True
    return render_template("zeus/home.html", e=e, cohorts=zeus_service.cohorts_for(current_user), splash=splash,
                           enabled=zeus_service.enabled(),
                           week=zeus_service.week_overview(current_user, week),
                           invitations=zeus_service.invitations_for(e) if e else [],
                           notifications=zeus_service.notifications_for(current_user, limit=3) if e else [],
                           classes=zeus_service.classes_for_student(e) if e else [],
                           coach_invitations=[] if e else zeus_service.coach_invitations_for(current_user),
                           messages_unread=zeus_message_service.unread_count(current_user),
                           birthdays=zeus_birthday_service.home(current_user, zeus_birthday_service.today()),
                           birthday_scopes=zeus_birthday_service.SCOPES)


THEME_COOKIE_DAYS = 365


@bp.post("/theme")
def theme():
    """Day / night mode of Zeus, per browser: a cookie read by zeus/base.html (no account setting to keep)."""
    response = redirect(_safe_next(request.form.get("next")))
    response.set_cookie("zeus_theme", "dark" if request.form.get("theme") == "dark" else "light",
                        max_age=THEME_COOKIE_DAYS * 24 * 3600, samesite="Lax", secure=request.is_secure, httponly=True)
    return response


@bp.post("/mode")
def mode():
    if current_user.role != ROLE_ADMIN:
        abort(403)
    on = request.form.get("enabled") == "1"
    zeus_service.set_enabled(on)
    flash("Mode Zeus activé : les utilisateurs Zeus y arrivent à la connexion." if on
          else "Mode Zeus désactivé : retour à l'interface classique pour tous.", "success")
    return redirect(url_for("zeus.home"))


# ---- cohorts: classes, courses, coaches

@bp.get("/cohortes")
def cohorts():
    """The cohorts one sees in Zeus (zeus_service.cohorts_for): a coach also those of the classes he teaches."""
    if zeus_service.student_enrollment(current_user):
        abort(404)
    return render_template("zeus/cohorts.html", cohorts=zeus_service.cohorts_for(current_user))


@bp.get("/cohortes/<int:cohort_id>")
def cohort(cohort_id: int):
    c = _cohort(cohort_id)
    # The classic enrollment page (cohort.detail): the desk, or a coach the admin gave the cohort to.
    can_enrollments = not current_user.is_coach or any(cc.user_id == current_user.id for cc in c.coaches)
    return render_template("zeus/cohort.html", cohort=c, classes=zeus_service.classes_of(c),
                           courses=zeus_service.courses_of(c),
                           coaches=zeus_service.cohort_coaches(c),
                           can_manage=zeus_service.can_manage(current_user, c), can_enrollments=can_enrollments)


@bp.post("/cohortes/<int:cohort_id>/classes")
def class_create(cohort_id: int):
    c = _cohort(cohort_id)
    _require_manage(c)
    try:
        klass = zeus_service.create_class(c, request.form.get("name", ""), current_user)
        flash(f"Classe « {klass.name} » créée : invitez les étudiants.", "success")
        return redirect(url_for("zeus.klass", class_id=klass.id))
    except BusinessError as exc:
        flash(str(exc), "danger")
        return redirect(url_for("zeus.cohort", cohort_id=c.id))


@bp.post("/cohortes/<int:cohort_id>/cours")
def course_create(cohort_id: int):
    c = _cohort(cohort_id)
    _require_manage(c)
    try:
        course = zeus_service.save_course(c, request.form)
        flash(f"Cours « {course.name} » ajouté.", "success")
    except BusinessError as exc:
        db.session.rollback()
        flash(str(exc), "danger")
    return redirect(url_for("zeus.cohort", cohort_id=c.id) + "#cours")


@bp.route("/cours/<int:course_id>", methods=["GET", "POST"])
def course(course_id: int):
    course = db.get_or_404(ZeusCourse, course_id)
    c = _cohort(course.cohort_id)
    if request.method == "POST":
        _require_manage(c)
        try:
            zeus_service.save_course(c, request.form, course)
            flash("Cours enregistré.", "success")
        except BusinessError as exc:
            db.session.rollback()
            flash(str(exc), "danger")
        return redirect(url_for("zeus.course", course_id=course.id))
    return render_template("zeus/course.html", course=course, cohort=c, coaches=zeus_service.cohort_coaches(c),
                           can_manage=zeus_service.can_manage(current_user, c))


# ---- classes and invitations

@bp.get("/classes")
def classes():
    """Staff: the classes of their cohorts. Student: his classes and the invitations to answer."""
    e = zeus_service.student_enrollment(current_user)
    if e:
        return render_template("zeus/my_classes.html", e=e, classes=zeus_service.classes_for_student(e),
                               invitations=zeus_service.invitations_for(e))
    return render_template("zeus/classes.html", cohorts=[(c, zeus_service.classes_of(c),
                                                          zeus_service.can_manage(current_user, c))
                                                         for c in zeus_service.cohorts_for(current_user)],
                           coach_invitations=zeus_service.coach_invitations_for(current_user))


@bp.route("/classes/<int:class_id>", methods=["GET", "POST"])
def klass(class_id: int):
    k = db.get_or_404(ZeusClass, class_id)
    c = _cohort(k.cohort_id)
    if zeus_service.student_enrollment(current_user):
        abort(404)  # students see their classes on /zeus/classes
    is_admin = current_user.role == ROLE_ADMIN
    if request.method == "POST":
        action = request.form.get("action", "")
        if not zeus_service.can_manage_class(current_user, k) or (action in ("edit", "delete") and not is_admin):
            abort(403)
        try:
            if action == "delete":
                name = k.name
                zeus_service.delete_class(k)
                flash(f"Classe « {name} » supprimée.", "success")
                return redirect(url_for("zeus.cohort", cohort_id=c.id))
            if action == "edit":
                zeus_service.update_class(k, request.form)
                flash("Classe enregistrée.", "success")
            elif action in ("coach_update", "coach_remove"):
                link = next((x for x in k.coaches if x.id == request.form.get("link_id", type=int)), None)
                if link is None or (link.is_manager and not is_admin):
                    abort(403)  # a manager handles the other coaches, the admin everyone
                if action == "coach_remove":
                    name = link.user.display_name
                    zeus_service.remove_coach(link)
                    flash(f"{name} retiré de la classe.", "success")
                else:
                    zeus_service.update_coach(link, request.form, current_user)
                    flash(f"{link.user.display_name} : {link.function}.", "success")
            elif action == "coach_add":
                link = zeus_service.add_coach(k, request.form, current_user)
                flash(f"{link.user.display_name} ajouté à la classe ({link.function})." if link.accepted_at else
                      f"{link.user.display_name} invité ({link.function}) : il accepte depuis son espace Zeus.",
                      "success")
            elif action == "remove":
                member = next((m for m in k.members if m.id == request.form.get("member_id", type=int)), None)
                if member:
                    zeus_service.remove_member(member)
                    flash("Étudiant retiré de la classe.", "success")
            else:
                n = zeus_service.invite(k, request.form.getlist("enrollment_ids", type=int), current_user)
                flash(f"{n} étudiant(s) invité(s) : ils rejoignent la classe en acceptant depuis leur espace Zeus.",
                      "success" if n else "warning")
        except BusinessError as exc:
            db.session.rollback()
            flash(str(exc), "danger")
        return redirect(url_for("zeus.klass", class_id=k.id))
    can_manage = zeus_service.can_manage_class(current_user, k)
    today = zeus_birthday_service.today()
    return render_template("zeus/class.html", k=k, cohort=c, invitable=zeus_service.invitable(k),
                           can_manage=can_manage, is_admin=is_admin, roles=zeus_service.class_roles(),
                           can_message=zeus_message_service.on_team(k, current_user),
                           birthdays=zeus_birthday_service.upcoming(zeus_birthday_service.class_people(k), today),
                           birthdays_on=zeus_birthday_service.scope() != zeus_birthday_service.SCOPE_OFF, today=today,
                           coach_choices=zeus_service.coach_choices(k) if can_manage else [])


@bp.post("/invitations-coach/<int:link_id>")
def answer_coach(link_id: int):
    link = db.get_or_404(ZeusClassCoach, link_id)
    if link.user_id != current_user.id or link.accepted_at:
        abort(404)
    accept = request.form.get("accept") == "1"
    name = link.klass.name
    zeus_service.answer_coach(link, accept)
    flash(f"Vous êtes coach de la classe « {name} »." if accept else "Invitation refusée.",
          "success" if accept else "info")
    return _back(url_for("zeus.classes"))


@bp.route("/roles-de-classe", methods=["GET", "POST"])
def class_roles():
    """Admin: the functions a coach may hold in a class."""
    if current_user.role != ROLE_ADMIN:
        abort(403)
    if request.method == "POST":
        role = db.session.get(ZeusClassRole, request.form.get("role_id", 0, type=int))
        try:
            if role and "delete" in request.form:
                zeus_service.delete_class_role(role)
                flash(f"Rôle « {role.name} » supprimé.", "success")
            else:
                role = zeus_service.save_class_role(request.form, role)
                flash(f"Rôle « {role.name} » enregistré.", "success")
        except BusinessError as exc:
            db.session.rollback()
            flash(str(exc), "danger")
        return redirect(url_for("zeus.class_roles"))
    roles = zeus_service.class_roles()
    used = {r.id: sum(1 for c in db.session.scalars(db.select(ZeusClassCoach).where(ZeusClassCoach.role_id == r.id)))
            for r in roles}
    return render_template("zeus/class_roles.html", roles=roles, used=used)


@bp.post("/invitations/<int:member_id>")
def answer(member_id: int):
    member = db.get_or_404(ZeusClassMember, member_id)
    e = zeus_service.student_enrollment(current_user)
    if e is None or member.enrollment_id != e.id or member.joined_at:
        abort(404)
    accept = request.form.get("accept") == "1"
    name = member.klass.name
    zeus_service.answer(member, accept)
    flash(f"Vous avez rejoint la classe « {name} »." if accept else "Invitation refusée.",
          "success" if accept else "info")
    return _back(url_for("zeus.classes"))


# ---- notifications

@bp.route("/notifications", methods=["GET", "POST"])
def notifications():
    if request.method == "POST":
        cohort_id, _, target = request.form.get("target", "").partition("/")  # "3/cohort", "3/class:5"...
        c = _cohort(int(cohort_id) if cohort_id.isdigit() else 0)
        try:  # who may write to which target: send_notification
            n = zeus_service.send_notification(c, target, request.form, current_user)
            flash(f"Notification envoyée : {n.target_label}.", "success")
        except BusinessError as exc:
            db.session.rollback()
            flash(str(exc), "danger")
        return redirect(url_for("zeus.notifications"))
    return render_template("zeus/notifications.html", notifications=zeus_service.notifications_for(current_user),
                           targets=zeus_service.notification_targets(current_user),
                           student=zeus_service.student_enrollment(current_user) is not None)


@bp.post("/notifications/<int:notification_id>/supprimer")
def notification_delete(notification_id: int):
    n = db.get_or_404(ZeusNotification, notification_id)
    _cohort(n.cohort_id)
    if current_user.role != ROLE_ADMIN and n.created_by != current_user.id:
        abort(403)
    zeus_service.delete_notification(n)
    flash("Notification supprimée.", "success")
    return redirect(url_for("zeus.notifications"))


# ---- class messaging (one-to-one: a student and one coach of his class; the admin reviews; not real time)

@bp.get("/messages")
def messages():
    """Student: his classes, one row per coach. Coach: his own conversations. Admin: review of every conversation."""
    e = zeus_service.student_enrollment(current_user)
    if e:
        return render_template("zeus/messages.html", e=e, groups=zeus_message_service.student_inbox(e, current_user),
                               author=zeus_message_service.author)
    review = zeus_message_service.is_reviewer(current_user)
    class_id, awaiting = request.args.get("classe", type=int), request.args.get("a_repondre") == "1"
    coach_id = request.args.get("coach", type=int) if review else None
    page = zeus_message_service.inbox(current_user, class_id, awaiting, request.args.get("page", 1, type=int),
                                      coach_id=coach_id)
    convs = list(page.items)
    mine = [c for c in convs if c.coach_id == current_user.id]  # the review has no unread state, his own chats do
    return render_template("zeus/messages.html", e=None, review=review, page=page, convs=convs,
                           last=zeus_message_service.last_messages(convs),
                           unread=zeus_message_service.unread_ids(current_user, mine),
                           classes=zeus_message_service.inbox_classes(current_user), class_id=class_id,
                           coaches=zeus_message_service.review_coaches() if review else [], coach_id=coach_id,
                           awaiting=awaiting, args={k: v for k, v in request.args.items() if k != "page"},
                           author=zeus_message_service.author)


@bp.get("/messages/non-lus")
def unread():
    """The menu badge, polled by app.js (initUnread)."""
    return jsonify(count=zeus_message_service.unread_count(current_user))


@bp.route("/messages/<int:class_id>/<int:enrollment_id>/<int:coach_id>", methods=["GET", "POST"])
def conversation(class_id: int, enrollment_id: int, coach_id: int):
    k = db.get_or_404(ZeusClass, class_id)
    e = db.get_or_404(Enrollment, enrollment_id)
    coach = db.get_or_404(User, coach_id)
    _cohort(k.cohort_id)
    if not zeus_message_service.can_read(current_user, k, e, coach):
        abort(404)
    can_write = zeus_message_service.can_write(current_user, k, e, coach)
    here = {"class_id": k.id, "enrollment_id": e.id, "coach_id": coach.id}
    if request.method == "POST":
        if not can_write:
            abort(403)
        try:
            zeus_message_service.send(k, e, coach, request.form.get("body", ""), current_user)
        except BusinessError as exc:
            db.session.rollback()
            flash(str(exc), "danger")  # no anchor: the message shows at the top of the page
            return redirect(url_for("zeus.conversation", **here))
        return redirect(url_for("zeus.conversation", **here, _anchor="fin"))
    conv = zeus_message_service.conversation(k, e, coach)
    participant = zeus_message_service.is_participant(current_user, k, e, coach)
    if conv and participant:
        zeus_message_service.mark_read(conv, current_user)
    elif conv:
        zeus_message_service.review(conv, current_user)  # the admin: audit logged, every time
    return render_template("zeus/conversation.html", k=k, e=e, coach=coach, conv=conv, can_write=can_write,
                           student=zeus_service.student_enrollment(current_user) is not None,
                           review=not participant, author=zeus_message_service.author,
                           coach_link=zeus_service.class_link(coach, k),
                           member=zeus_message_service.is_member(k, e), max_length=zeus_message_service.MAX_LENGTH)


# ---- birthdays (in the app only, worked out when a page is shown)

@bp.post("/anniversaire")
def birthday_save():
    """The home prompt: give one's birthday (or hide it), or "Plus tard"."""
    try:
        if request.form.get("action") == "later":
            zeus_birthday_service.snooze(current_user, zeus_birthday_service.today())
            flash("Nous vous le redemanderons dans un mois.", "info")
        else:
            zeus_birthday_service.save_birthday(current_user, request.form)
            flash("Anniversaire enregistré. Vous pouvez le modifier depuis votre profil.", "success")
    except BusinessError as exc:
        db.session.rollback()
        flash(str(exc), "danger")
    return redirect(url_for("zeus.home"))


@bp.post("/anniversaire/reglages")
def birthday_settings():
    if current_user.role != ROLE_ADMIN:
        abort(403)
    try:
        zeus_birthday_service.save_settings(request.form.get("scope", ""), request.form.get("wishes") == "1",
                                            current_user)
        flash("Réglages des anniversaires enregistrés.", "success")
    except BusinessError as exc:
        db.session.rollback()
        flash(str(exc), "danger")
    return redirect(url_for("zeus.home"))


@bp.post("/anniversaire/voeux/<int:user_id>")
def birthday_wish(user_id: int):
    person = zeus_birthday_service.find(current_user, user_id)
    if person is None:
        abort(404)
    try:
        zeus_birthday_service.add_wish(current_user, person, request.form.get("body", ""),
                                       zeus_birthday_service.today())
        flash(f"Vœu envoyé à {person.name}.", "success")
    except BusinessError as exc:
        db.session.rollback()
        flash(str(exc), "danger")
    return redirect(url_for("zeus.home"))


@bp.post("/anniversaire/voeux/<int:wish_id>/supprimer")
def birthday_wish_delete(wish_id: int):
    wish = db.get_or_404(ZeusBirthdayWish, wish_id)
    if wish.recipient_id != current_user.id:
        abort(404)
    zeus_birthday_service.delete_wish(wish)
    flash("Vœu supprimé.", "success")
    return redirect(url_for("zeus.home"))


@bp.get("/classes/<int:class_id>/anniversaires")
def class_birthdays(class_id: int):
    """The class's birthdays of a month (?mois=YYYY-MM), printable: for its teaching staff."""
    k = db.get_or_404(ZeusClass, class_id)
    _cohort(k.cohort_id)
    if not zeus_service.can_teach_class(current_user, k):
        abort(404)
    first = zeus_birthday_service.month_start(request.args.get("mois"), zeus_birthday_service.today())
    last = (first + timedelta(days=32)).replace(day=1) - timedelta(days=1)
    return render_template("zeus/class_birthdays.html", k=k, first=first,
                           rows=zeus_birthday_service.in_range(zeus_birthday_service.class_people(k), first, last),
                           off=zeus_birthday_service.scope() == zeus_birthday_service.SCOPE_OFF,
                           prev=(first - timedelta(days=1)).strftime("%Y-%m"),
                           next=(last + timedelta(days=1)).strftime("%Y-%m"))


# ---- calendar: day, week and month views of the sessions, activities and birthdays; programming sessions

def _calendar_context(args):
    """The view and the filters (args: the URL, or the form sent back when it is shown again)."""
    cohorts = zeus_service.cohorts_for(current_user)
    selected = None
    if args.get("cohort", type=int):
        selected = db.get_or_404(Cohort, args.get("cohort", type=int))
        if selected not in cohorts:
            abort(404)
    view = zeus_calendar_service.parse_view(args, request.cookies.get(zeus_calendar_service.VIEW_COOKIE),
                                            candidate_service.today())
    return view, zeus_calendar_service.resolve_scope(current_user, args, cohorts, selected)


def _schedule_target(scope):
    """The cohort the « Programmer » form writes to: the one in view (filter, or the only one) where the user may
    schedule; (target, may schedule elsewhere: choose a cohort)."""
    def allowed(c):
        classes, full = zeus_service.schedulable(current_user, c)
        return bool(classes or full)
    in_view = scope.cohort or (scope.cohorts[0] if len(scope.cohorts) == 1 else None)
    if in_view is not None:
        return (in_view if allowed(in_view) else None), False
    manageable = [c for c in scope.cohorts if allowed(c)]
    return (manageable[0], False) if len(manageable) == 1 else (None, bool(manageable))


def _calendar_page(args):
    view, scope = _calendar_context(args)
    events, sessions = zeus_calendar_service.collect(current_user, view, scope)
    days = zeus_calendar_service.by_day(events, view)
    target, choose = _schedule_target(scope)
    form = None
    if target is not None:
        classes, full = zeus_service.schedulable(current_user, target)
        form = {"classes": classes, "full": full, "courses": zeus_service.courses_of(target),
                "coaches": zeus_service.cohort_coaches(target)}
    return render_template(
        "zeus/calendar.html", view=view, scope=scope, keep=scope.args, views=zeus_calendar_service.VIEWS,
        days=days, month_chips=zeus_calendar_service.MONTH_CHIPS,
        grid=zeus_calendar_service.build_grid(days, view.start.tzinfo) if view.kind != "mois" else None,
        counts={"sessions": len(sessions), "activities": sum(1 for e in events if e.kind == "activity")},
        has_birthdays=any(d.birthdays for d in days), today=candidate_service.today(),
        target=target, choose=choose, form=form)


@bp.route("/calendrier", methods=["GET", "POST"])
def calendar():
    if request.method == "POST":
        c = _cohort(request.form.get("cohort_id", 0, type=int))
        k = next((k for k in zeus_service.classes_of(c) if k.id == request.form.get("class_id", type=int)), None)
        if not zeus_service.can_schedule(current_user, c, k):
            abort(403)
        try:
            s = zeus_service.create_session(c, request.form, current_user)
            flash(f"Séance programmée : {s.course.name}.", "success")
            return redirect(url_for("zeus.calendar", week=request.form.get("starts_at", "")[:10], cohort=c.id))
        except BusinessError as exc:
            db.session.rollback()
            flash(str(exc), "danger")
        return redirect(url_for("zeus.calendar", cohort=c.id))
    response = make_response(_calendar_page(request.args))
    if request.args.get("vue") in zeus_calendar_service.VIEWS:  # the last view used comes back next time
        response.set_cookie(zeus_calendar_service.VIEW_COOKIE, request.args["vue"],
                            max_age=THEME_COOKIE_DAYS * 24 * 3600, samesite="Lax", secure=request.is_secure,
                            httponly=True)
    return response


# ---- sessions and roll calls

def _session(session_id: int) -> ZeusSession:
    s = db.get_or_404(ZeusSession, session_id)
    _cohort(s.course.cohort_id)
    e = zeus_service.student_enrollment(current_user)
    if e and s.class_id and s.class_id not in {k.id for k in zeus_service.classes_for_student(e)}:
        abort(404)
    return s


@bp.route("/seances/<int:session_id>", methods=["GET", "POST"])
def session(session_id: int):
    s = _session(session_id)
    can_call = zeus_service.can_call(current_user, s)
    if request.method == "POST":
        if not can_call:
            abort(403)
        if request.form.get("action") == "delete":
            if not zeus_service.can_schedule(current_user, s.course.cohort, s.klass):
                abort(403)
            zeus_service.delete_session(s)
            flash("Séance supprimée.", "success")
            return redirect(url_for("zeus.calendar", cohort=s.course.cohort_id))
        n = zeus_service.save_roll_call(s, set(request.form.getlist("present", type=int)), current_user)
        flash(f"Appel enregistré : {n} présent(s) sur {len(s.expected)}.", "success")
        return redirect(url_for("zeus.session", session_id=s.id))
    present = {m.enrollment_id for m in s.marks}
    if can_call and request.args.get("partial"):  # roll call by QR code: refreshed every 10 s
        return render_template("zeus/_call_live.html", s=s, present=present, qr=zeus_service.call_qr(s))
    return allow_camera(make_response(render_template(
        "zeus/session.html", s=s, present=present, can_call=can_call,
        can_manage=zeus_service.can_schedule(current_user, s.course.cohort, s.klass),
        qr=zeus_service.call_qr(s) if can_call and request.args.get("appel") == "qr" else None,
        me=zeus_service.student_enrollment(current_user))))


@bp.post("/seances/<int:session_id>/scan")
def session_scan(session_id: int):
    s = _session(session_id)
    if not zeus_service.can_call(current_user, s):
        abort(403)
    try:
        e, new = zeus_service.scan(s, request.form.get("token", ""), current_user)
        flash(f"{e.candidate.full_name} ({e.matricule}) : " + ("présent." if new else "déjà marqué présent."),
              "success")
    except BusinessError as exc:
        flash(str(exc), "danger")
    return redirect(url_for("zeus.session", session_id=s.id))


@bp.route("/appels", methods=["GET", "POST"])
def roll_calls():
    """Staff: the roll calls of the last 30 days. Student: his attendance, and the coach's QR code to scan (in-page
    scanner, or the phone camera opening ?code=...)."""
    e = zeus_service.student_enrollment(current_user)
    if e:
        if request.method == "POST":
            try:
                s, new = zeus_service.self_mark(e, request.form.get("token", ""), current_user)
                flash(f"{s.course.name} · {e.matricule} : " + ("présence enregistrée." if new else "déjà présent(e)."),
                      "success")
            except BusinessError as exc:
                flash(str(exc), "danger")
            return redirect(url_for("zeus.roll_calls"))
        return allow_camera(make_response(render_template(
            "zeus/my_attendance.html", e=e, rows=zeus_service.student_attendance(e, current_user),
            code=request.args.get("code", "")[:200])))
    if request.method == "POST":
        abort(403)
    end = zeus_service.week_start(None) + timedelta(days=7)
    sessions = zeus_service.sessions_for(current_user, end - timedelta(days=37), end)
    if current_user.role == ROLE_TRADE_COACH:
        sessions = [s for s in sessions if s.coach_id == current_user.id]  # coach métier: his sessions only
    return render_template("zeus/roll_calls.html", sessions=list(reversed(sessions)))
