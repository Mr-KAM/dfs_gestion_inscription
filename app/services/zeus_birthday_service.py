"""Zeus birthdays. Shown in the app only, worked out when a page is displayed (there is no scheduler): a banner on the
Zeus home on the day, the calendar, the class page and a printable monthly list per class.

Who sees whose birthday follows the admin's level, from shared classes or the shared cohort at read time:
off; "class": the people one shares a class with (students and their coaches); "cohort": everyone of one's training
cohort. The admin sees them all (unless off). Only the day and month are ever shown, never the year; anyone may hide
his birthday. A birthday on a Saturday or Sunday is also celebrated on the Friday before and the Monday after.

A student's birthday is his Candidate.birth_date (administrative record); a staff member gives the day and month only
(User.birthday_day / birthday_month)."""
import calendar
from dataclasses import dataclass, field
from datetime import date, timedelta

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from app.extensions import db
from app.models import Candidate, Enrollment, User, ZeusBirthdayWish, ZeusClass
from app.models.user import COACH_ROLES, ROLE_ADMIN
from app.services import account_service, audit_service, candidate_service, settings_service, zeus_service
from app.services.candidate_service import BusinessError
from app.utils import utcnow

SCOPE_OFF, SCOPE_CLASS, SCOPE_COHORT = "off", "class", "cohort"
SCOPES = {SCOPE_OFF: "Désactivés", SCOPE_CLASS: "Classe", SCOPE_COHORT: "Cohorte"}
WISH_MAX = 280
WISH_KEEP_DAYS = 30   # wishes are purged after a month
SNOOZE_DAYS = 30      # "Plus tard" on the home prompt
UPCOMING_DAYS = 30    # the class page's "Prochains anniversaires"
THANKS_DAYS = 7       # the birthday person sees the wishes received this long after the day
MONTHS = ("janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre",
          "novembre", "décembre")
STUDENT_LABEL = "Étudiant(e)"


def today() -> date:
    """The school's date (time zone setting); a function so tests can move it."""
    return candidate_service.today()


# ---- settings

def scope() -> str:
    value = settings_service.get("zeus_birthday_scope")
    return value if value in SCOPES else SCOPE_CLASS


def wishes_enabled() -> bool:
    return settings_service.get("zeus_birthday_wishes") != "0"


def save_settings(new_scope: str, wishes: bool, user) -> None:
    if new_scope not in SCOPES:
        raise BusinessError("Niveau d'affichage inconnu.")
    settings_service.set_many({"zeus_birthday_scope": new_scope, "zeus_birthday_wishes": "1" if wishes else "0"})
    audit_service.log("settings_update", None, {"zeus_birthday_scope": new_scope, "zeus_birthday_wishes": wishes},
                      user=user)
    db.session.commit()


# ---- dates

def occurrence(day: int, month: int, year: int) -> date:
    """The birthday in a given year; 29 February is celebrated on the 28th in other years."""
    if (month, day) == (2, 29) and not calendar.isleap(year):
        return date(year, 2, 28)
    return date(year, month, day)


def window(occ: date) -> tuple[date, date]:
    """When it is celebrated: the day itself; Friday to Monday for a weekend birthday."""
    if occ.weekday() == 5:
        return occ - timedelta(days=1), occ + timedelta(days=2)
    if occ.weekday() == 6:
        return occ - timedelta(days=2), occ + timedelta(days=1)
    return occ, occ


def day_month(d: date) -> str:
    """"1er juin", "12 juin": never the year (Jinja filter day_month)."""
    return f"{'1er' if d.day == 1 else d.day} {MONTHS[d.month - 1]}"


def month_start(value: str | None, default: date) -> date:
    """The first day of the month "YYYY-MM"; the default's month when missing or invalid."""
    try:
        year, month = (int(part) for part in (value or "").split("-"))
        return date(year, month, 1)
    except ValueError:
        return default.replace(day=1)


# ---- people

@dataclass
class Person:
    key: str              # "e<enrollment id>" (student) or "u<user id>" (staff): one row per person
    name: str
    day: int
    month: int
    user: User | None     # who receives the wishes: the student's account, the staff member
    label: str            # "Étudiant(e)", or the staff member's function


def _student(e: Enrollment) -> Person | None:
    c = e.candidate
    if c is None or c.birth_date is None or (c.account is not None and c.account.hide_birthday):
        return None
    return Person(f"e{e.id}", c.full_name, c.birth_date.day, c.birth_date.month, c.account, STUDENT_LABEL)


def _staff(user: User, label: str | None = None) -> Person | None:
    if not (user.birthday_day and user.birthday_month) or user.hide_birthday:
        return None
    return Person(f"u{user.id}", user.display_name, user.birthday_day, user.birthday_month, user,
                  label or user.role_label)


def _enrollments(cohort_ids=None, enrollment_ids=None) -> list[Enrollment]:
    """Registered students (DFS matricule), with candidate and account in one go."""
    stmt = (select(Enrollment).where(Enrollment.matricule.is_not(None), Enrollment.matricule != "")
            .options(selectinload(Enrollment.candidate).selectinload(Candidate.account)))
    if cohort_ids is not None:
        stmt = stmt.where(Enrollment.cohort_id.in_(cohort_ids))
    if enrollment_ids is not None:
        stmt = stmt.where(Enrollment.id.in_(enrollment_ids))
    return list(db.session.scalars(stmt))


def _people(enrollments, staff, viewer=None) -> list[Person]:
    """Students then staff, once each, without the viewer; nobody without a birthday or who hides it."""
    found: dict[str, Person] = {}
    for p in [_student(e) for e in enrollments] + [_staff(u, label) for u, label in staff]:
        if p is not None and not (viewer is not None and p.user is not None and p.user.id == viewer.id):
            found.setdefault(p.key, p)
    return list(found.values())


def circle(viewer) -> list[Person]:
    """The people whose birthday the viewer sees, per the admin's level (himself excluded)."""
    level = scope()
    if level == SCOPE_OFF:
        return []
    e = zeus_service.student_enrollment(viewer)
    if viewer.role == ROLE_ADMIN or level == SCOPE_COHORT:
        if viewer.role == ROLE_ADMIN:
            cohorts = zeus_service.cohorts_for(viewer)  # every cohort
        elif e is not None:
            cohorts = [e.cohort]
        else:
            cohorts = zeus_service.cohorts_for(viewer) if viewer.role in COACH_ROLES else []
        staff = [(u, None) for c in cohorts for u in zeus_service.cohort_coaches(c)]
        return _people(_enrollments(cohort_ids=[c.id for c in cohorts]), staff, viewer)
    classes = (zeus_service.classes_for_student(e) if e is not None
               else [link.klass for link in viewer.zeus_class_links if link.accepted_at])
    members = [m.enrollment_id for k in classes for m in k.members if m.joined_at]
    staff = [(link.user, link.function) for k in classes for link in k.staff]
    return _people(_enrollments(enrollment_ids=members), staff, viewer)


def class_people(klass: ZeusClass) -> list[Person]:
    """The class's students and coaches (class page, monthly list); nobody when birthdays are off."""
    if scope() == SCOPE_OFF:
        return []
    members = [m.enrollment_id for m in klass.members if m.joined_at]
    return _people(_enrollments(enrollment_ids=members), [(link.user, link.function) for link in klass.staff])


def find(viewer, user_id: int) -> Person | None:
    """A person of the viewer's circle, by account (wishes)."""
    return next((p for p in circle(viewer) if p.user is not None and p.user.id == user_id), None)


# ---- ranges

def in_range(people: list[Person], start: date, end: date) -> list[tuple[Person, date]]:
    """The birthdays falling between two dates (inclusive), by date then name."""
    found = []
    for p in people:
        for year in range(start.year, end.year + 1):
            occ = occurrence(p.day, p.month, year)
            if start <= occ <= end:
                found.append((p, occ))
    return sorted(found, key=lambda x: (x[1], x[0].name))


def upcoming(people: list[Person], day: date) -> list[tuple[Person, date]]:
    return in_range(people, day, day + timedelta(days=UPCOMING_DAYS))


def by_day(pairs: list[tuple[Person, date]]) -> dict[str, list[Person]]:
    """{"YYYY-MM-DD": [people]} (calendar)."""
    days: dict[str, list[Person]] = {}
    for p, occ in pairs:
        days.setdefault(occ.isoformat(), []).append(p)
    return days


def celebrated(people: list[Person], day: date) -> list[tuple[Person, date]]:
    """The birthdays celebrated on a day (weekend ones from Friday to Monday), with their date."""
    found = []
    for p in people:
        for year in (day.year - 1, day.year, day.year + 1):  # a 1st of January on a Saturday: from 31 December
            occ = occurrence(p.day, p.month, year)
            start, end = window(occ)
            if start <= day <= end:
                found.append((p, occ))
                break
    return sorted(found, key=lambda x: (x[1], x[0].name))


# ---- one's own birthday: the home prompt

def _own(user) -> Person | None:
    """One's own birthday, even hidden (the greeting is for oneself)."""
    if user.is_candidate:
        c = user.candidate
        if c is None or c.birth_date is None:
            return None
        return Person(f"self{user.id}", c.full_name, c.birth_date.day, c.birth_date.month, user, STUDENT_LABEL)
    if not (user.birthday_day and user.birthday_month):
        return None
    return Person(f"self{user.id}", user.display_name, user.birthday_day, user.birthday_month, user, "")


def needs_prompt(user, day: date) -> bool:
    """The Zeus home asks students and coaches for a missing birthday, unless hidden or snoozed ("Plus tard")."""
    if scope() == SCOPE_OFF or user.hide_birthday or _own(user) is not None:
        return False
    if zeus_service.student_enrollment(user) is None and user.role not in COACH_ROLES:
        return False
    return not (user.birthday_prompt_snoozed_until and user.birthday_prompt_snoozed_until > day)


def save_birthday(user, form) -> None:
    """The home prompt: a student's full birth date (his record), a staff member's day and month; or "hide"."""
    hide = form.get("hide_birthday") == "1"
    if user.is_candidate:
        value = form.get("birth_date", "")
        if value:
            user.candidate.birth_date = account_service._parse_birth_date(value)
        elif not hide:
            raise BusinessError("Indiquez votre date de naissance.")
    else:
        given = account_service.parse_day_month(form.get("birthday_day"), form.get("birthday_month"))
        if given:
            user.birthday_day, user.birthday_month = given
        elif not hide:
            raise BusinessError("Indiquez le jour et le mois de votre anniversaire.")
    user.hide_birthday = hide
    audit_service.log("profile_update", user, {"birthday": True, "hidden": hide}, user=user)
    db.session.commit()


def snooze(user, day: date) -> None:
    user.birthday_prompt_snoozed_until = day + timedelta(days=SNOOZE_DAYS)
    db.session.commit()


# ---- wishes

def _purge() -> None:
    """Delete the wishes older than a month (lazily: there is no scheduler). The callers commit right after, which
    expires the session, so it is not synchronized here."""
    db.session.execute(delete(ZeusBirthdayWish).where(
        ZeusBirthdayWish.created_at < utcnow() - timedelta(days=WISH_KEEP_DAYS))
        .execution_options(synchronize_session=False))


def add_wish(author, person: Person, body: str, day: date) -> ZeusBirthdayWish:
    """The caller found the person in the author's circle. One wish per author and birthday, while it is celebrated.
    Audit logged without the text."""
    if not wishes_enabled():
        raise BusinessError("Les vœux d'anniversaire sont désactivés.")
    now = celebrated([person], day)
    if person.user is None or not now:
        raise BusinessError("Ce n'est pas le moment de souhaiter son anniversaire.")
    occ = now[0][1]
    body = (body or "").strip()
    if not body:
        raise BusinessError("Écrivez votre vœu.")
    if len(body) > WISH_MAX:
        raise BusinessError(f"Vœu trop long : {WISH_MAX} caractères au maximum.")
    already = BusinessError("Vous avez déjà souhaité son anniversaire.")
    if db.session.scalar(select(ZeusBirthdayWish.id).where(ZeusBirthdayWish.recipient_id == person.user.id,
                                                           ZeusBirthdayWish.author_id == author.id,
                                                           ZeusBirthdayWish.birthday == occ)):
        raise already
    wish = ZeusBirthdayWish(recipient_id=person.user.id, author_id=author.id, birthday=occ, body=body)
    db.session.add(wish)
    try:
        db.session.flush()
    except IntegrityError:  # the same wish sent twice at once
        db.session.rollback()
        raise already
    audit_service.log("zeus_birthday_wish", wish, {"recipient": person.user.email, "birthday": occ.isoformat()},
                      user=author)
    _purge()
    db.session.commit()
    return wish


def wishes_received(user, day: date) -> list[ZeusBirthdayWish]:
    """The wishes of one's latest birthday, shown on the home for a week after it."""
    _purge()
    db.session.commit()
    return list(db.session.scalars(select(ZeusBirthdayWish).where(
        ZeusBirthdayWish.recipient_id == user.id,
        ZeusBirthdayWish.birthday.between(day - timedelta(days=THANKS_DAYS), day + timedelta(days=2)))
        .order_by(ZeusBirthdayWish.created_at)))


def wished_ids(author, day: date) -> set[int]:
    """The people this author already wished, for the birthdays celebrated around this day."""
    return set(db.session.scalars(select(ZeusBirthdayWish.recipient_id).where(
        ZeusBirthdayWish.author_id == author.id,
        ZeusBirthdayWish.birthday.between(day - timedelta(days=3), day + timedelta(days=3)))))


def delete_wish(wish: ZeusBirthdayWish) -> None:
    """By its recipient (the caller checks)."""
    db.session.delete(wish)
    db.session.commit()


# ---- the Zeus home

@dataclass
class Home:
    scope: str
    wishes_on: bool
    day: date | None = None
    prompt: bool = False
    student: bool = False                      # the prompt asks a student's full birth date
    mine: date | None = None                   # one's own birthday, while it is celebrated
    received: list = field(default_factory=list)
    celebrated: list = field(default_factory=list)  # [(Person, date)] of one's circle
    wished: set = field(default_factory=set)


def home(user, day: date) -> Home:
    h = Home(scope(), wishes_enabled(), day)
    if h.scope == SCOPE_OFF:
        return h
    h.prompt, h.student = needs_prompt(user, day), user.is_candidate
    own = _own(user)
    mine = celebrated([own], day) if own else []
    h.mine = mine[0][1] if mine else None
    h.received = wishes_received(user, day)
    h.celebrated = celebrated(circle(user), day)
    h.wished = wished_ids(user, day) if h.celebrated else set()
    return h
