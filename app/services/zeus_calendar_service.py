"""Zeus calendar: the day, week and month views of what a user sees (course sessions, the direction's activities,
birthdays), the time-grid layout. Presentation only: zeus_service saves
the sessions and says who may do what. Times are shown in the school's time zone."""
import calendar as calendar_module
import math
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

from app.models import Cohort, User, ZeusClass, ZeusSession
from app.models.user import COACH_ROLES
from app.services import direction_service, zeus_birthday_service, zeus_service
from app.services.zeus_birthday_service import MONTHS
from app.services.zeus_service import WEEKDAYS
from app.utils import as_utc, utcnow

VIEWS = {"jour": "Jour", "semaine": "Semaine", "mois": "Mois"}
DEFAULT_VIEW = "semaine"
VIEW_COOKIE = "zeus_cal_view"  # the last view used
DAY_START, DAY_END = 7, 20  # hours always on the time grid, widened to the events shown
MIN_MINUTES = 30  # an event is drawn (and laid out) at least this long
BIRTHDAYS_GROUPED = 3  # from this many birthdays on a day: one « N anniversaires » item
MONTH_CHIPS = 3  # items listed in a month cell, then « +N »


# ---- the view: which days, from the URL (or the remembered view)

@dataclass
class View:
    kind: str  # jour, semaine, mois
    anchor: date  # the day; the week's Monday; the month's first day
    days: list[date]  # shown (month: its whole weeks)
    start: datetime  # the first day at 00:00, school time zone
    end: datetime  # the day after the last one, 00:00
    prev: date
    next: date
    title: str
    weeks: list[list[date]] = field(default_factory=list)  # month: the rows of the grid


def parse_view(args, cookie: str | None, today: date) -> View:
    """?vue=jour|semaine|mois&date=YYYY-MM-DD. Without vue: the old ?week= link is a week, else the last view used.
    Dates outside 2000-2100: today."""
    kind = args.get("vue")
    if kind not in VIEWS:
        kind = "semaine" if args.get("week") else (cookie if cookie in VIEWS else DEFAULT_VIEW)
    anchor = zeus_service.parse_day(args.get("date") or args.get("week"), today)
    weeks = []
    if kind == "jour":
        days, prev, nxt = [anchor], anchor - timedelta(days=1), anchor + timedelta(days=1)
        title = f"{WEEKDAYS[anchor.weekday()].capitalize()} {anchor.day} {MONTHS[anchor.month - 1]} {anchor.year}"
    elif kind == "semaine":
        anchor -= timedelta(days=anchor.weekday())
        days = [anchor + timedelta(days=i) for i in range(7)]
        prev, nxt = anchor - timedelta(days=7), anchor + timedelta(days=7)
        title = f"Semaine du {days[0]:%d/%m} au {days[6]:%d/%m/%Y}"
    else:
        anchor = anchor.replace(day=1)
        weeks = calendar_module.Calendar(0).monthdatescalendar(anchor.year, anchor.month)
        days = [d for week in weeks for d in week]
        prev, nxt = (anchor - timedelta(days=1)).replace(day=1), (anchor + timedelta(days=32)).replace(day=1)
        title = f"{MONTHS[anchor.month - 1].capitalize()} {anchor.year}"
    tz = zeus_service.school_tz()
    return View(kind, anchor, days, datetime.combine(days[0], time(), tz),
                datetime.combine(days[-1] + timedelta(days=1), time(), tz), prev, nxt, title, weeks)


# ---- the filters (they apply to the sessions; activities and birthdays stay)

@dataclass
class Scope:
    cohorts: list[Cohort]  # every cohort the user sees
    cohort: Cohort | None = None  # cohort filter
    klass: ZeusClass | None = None
    coach_id: int | None = None
    mine: bool = False  # coach=moi: the sessions he gives
    classes: list[ZeusClass] = field(default_factory=list)  # class filter choices
    coaches: list[User] = field(default_factory=list)  # coach filter choices
    can_mine: bool = False  # « Mes séances » offered (teaching staff)

    @property
    def args(self) -> dict:
        """The filters for the page's links (None: left out of the URL)."""
        return {"cohort": self.cohort.id if self.cohort else None, "classe": self.klass.id if self.klass else None,
                "coach": "moi" if self.mine else self.coach_id}

    @property
    def filtered(self) -> bool:
        return bool(self.klass or self.coach_id)


def resolve_scope(user, args, cohorts: list[Cohort], selected: Cohort | None) -> Scope:
    """Only offered values count: a forged class or coach id is ignored (no name of another cohort shows).
    Staff choose a class or a coach once a single cohort is in view; a student, among his classes."""
    scope = Scope(cohorts=cohorts, cohort=selected)
    e = zeus_service.student_enrollment(user)
    one = selected or (cohorts[0] if len(cohorts) == 1 else None)
    if e is not None:
        choices = zeus_service.classes_for_student(e)
        scope.classes = choices if len(choices) > 1 else []
    else:
        choices = zeus_service.classes_of(one) if one else []
        scope.classes = choices
        scope.coaches = zeus_service.cohort_coaches(one) if one else []
        scope.can_mine = user.role in COACH_ROLES
    scope.klass = next((k for k in choices if k.id == args.get("classe", type=int)), None)
    coach = args.get("coach") or ""
    if coach == "moi" and scope.can_mine:
        scope.coach_id, scope.mine = user.id, True
    else:
        scope.coach_id = next((u.id for u in scope.coaches if str(u.id) == coach), None)
    return scope


# ---- events, per day

@dataclass
class Event:
    kind: str  # course, activity, birthday (birthdays: several of them on one day)
    title: str
    start: datetime | None = None  # school time zone; None for a birthday
    end: datetime | None = None
    day: date | None = None  # a birthday's day
    lines: list[str] = field(default_factory=list)
    status: tuple[str, str] | None = None  # roll call: (status css, label)
    obj: object = None  # ZeusSession, Activity, birthday Person (birthdays: the list of birthday events)


@dataclass
class Segment:
    """An event on one day: on the time grid, or in the day's all-day strip (events over several days, birthdays)."""
    event: Event
    label: str  # « 08:00 – 12:00 », « dès 08:00 », « jusqu'à 18:00 », « toute la journée »
    start_min: int = 0  # minutes from midnight, wall clock
    end_min: int = 0
    top: float = 0.0  # % of the grid's height
    height: float = 0.0
    col: int = 0
    cols: int = 1


@dataclass
class Day:
    date: date
    timed: list[Segment] = field(default_factory=list)
    all_day: list[Segment] = field(default_factory=list)
    birthdays: list[Event] = field(default_factory=list)

    @property
    def strip(self) -> list[Segment]:
        """The all-day items: events over several days, then the birthdays (one item from 3 on)."""
        if len(self.birthdays) >= BIRTHDAYS_GROUPED:
            group = Event("birthdays", f"{len(self.birthdays)} anniversaires", day=self.date, obj=self.birthdays)
            return self.all_day + [Segment(group, "")]
        return self.all_day + [Segment(b, "") for b in self.birthdays]

    @property
    def chips(self) -> list[Segment]:
        """The month cell's items: by time, then all day, then the birthdays."""
        return self.timed + self.strip

    @property
    def kinds(self) -> list[str]:
        """The month's coloured dots (phones)."""
        found = {s.event.kind for s in self.timed + self.all_day} | ({"birthday"} if self.birthdays else set())
        return [k for k in ("course", "activity", "birthday") if k in found]

    @property
    def empty(self) -> bool:
        return not (self.timed or self.all_day or self.birthdays)


def collect(user, view: View, scope: Scope, birthdays: bool = True) -> tuple[list[Event], list[ZeusSession]]:
    """What the user sees in the view: his sessions (filtered, crossing the range), the activities concerning him,
    the birthdays of his circle."""
    tz = view.start.tzinfo
    sessions = zeus_service.sessions_for(user, view.start, view.end, scope.cohort, klass=scope.klass,
                                         coach_id=scope.coach_id, overlap=True, cohorts=scope.cohorts)
    several = scope.cohort is None and len(scope.cohorts) > 1
    events = []
    for s in sessions:
        lines = [("Classe " + s.klass.name if s.klass else "Toute la cohorte")
                 + (f" · {s.course.cohort.name}" if several else "")]
        who = " · ".join(x for x in (s.coach.display_name if s.coach else "", s.room or "") if x)
        lines += [who] if who else []
        events.append(Event("course", s.course.name, as_utc(s.starts_at).astimezone(tz),
                            as_utc(s.ends_at).astimezone(tz), lines=lines, obj=s))
    for a in direction_service.activities_for(user, view.start, view.end):
        events.append(Event("activity", a.title, as_utc(a.starts_at).astimezone(tz), as_utc(a.ends_at).astimezone(tz),
                            lines=[a.place] if a.place else [], obj=a))
    if birthdays:
        for p, occ in zeus_birthday_service.in_range(zeus_birthday_service.circle(user), view.days[0], view.days[-1]):
            events.append(Event("birthday", p.name, day=occ, lines=[f"Anniversaire · {p.label}"], obj=p))
    return events, sessions


def by_day(events: list[Event], view: View) -> list[Day]:
    """Each event on its days. Within one day: on the grid; over several days (a seminar, a night session): in the
    all-day strip of each day, so the grid keeps its usual hours."""
    days = {d: Day(d) for d in view.days}
    for e in events:
        if e.kind == "birthday":
            if e.day in days:
                days[e.day].birthdays.append(e)
            continue
        first, last = e.start.date(), (e.end - timedelta(microseconds=1)).date()  # an end at 00:00: the day before
        if first == last:
            if first in days:
                end_min = 24 * 60 if e.end.date() > first else e.end.hour * 60 + e.end.minute
                days[first].timed.append(Segment(e, f"{e.start:%H:%M} – {e.end:%H:%M}",
                                                 e.start.hour * 60 + e.start.minute, end_min))
            continue
        day = max(first, view.days[0])
        while day <= min(last, view.days[-1]):
            if day == first and e.start.time() != time():
                label = f"dès {e.start:%H:%M}"
            elif day == last and e.end.date() == last:
                label = f"jusqu'à {e.end:%H:%M}"
            else:
                label = "toute la journée"
            days[day].all_day.append(Segment(e, label))
            day += timedelta(days=1)
    for day in days.values():
        day.timed.sort(key=lambda s: (s.start_min, -s.end_min))
    return list(days.values())


def hour_bounds(segments: list[Segment]) -> tuple[int, int]:
    """The grid's hours: DAY_START to DAY_END, widened to the events shown (whole hours, 0-24)."""
    lo, hi = DAY_START * 60, DAY_END * 60
    for s in segments:
        lo, hi = min(lo, s.start_min), max(hi, s.end_min, s.start_min + MIN_MINUTES)
    return max(0, lo // 60), min(24, math.ceil(hi / 60))


def layout(segments: list[Segment], lo: int, hi: int) -> None:
    """Places a day's timed segments on the grid [lo, hi) (minutes): overlapping ones side by side, each in the
    first free column of its cluster, at least MIN_MINUTES long. Sets top / height (% of the grid), col / cols."""
    span = max(hi - lo, 1)
    cluster, columns, cluster_end = [], [], 0
    for s in sorted(segments, key=lambda s: (s.start_min, -s.end_min)):
        end = max(s.end_min, s.start_min + MIN_MINUTES)
        if cluster and s.start_min >= cluster_end:  # nothing of the cluster still running: a new one
            for c in cluster:
                c.cols = len(columns)
            cluster, columns = [], []
        s.col = next((i for i, busy_until in enumerate(columns) if busy_until <= s.start_min), len(columns))
        if s.col == len(columns):
            columns.append(end)
        else:
            columns[s.col] = end
        cluster_end = max(cluster_end, end) if cluster else end
        cluster.append(s)
        top = max(s.start_min, lo)
        s.top, s.height = (top - lo) / span * 100, (min(end, hi) - top) / span * 100
    for c in cluster:
        c.cols = len(columns)


@dataclass
class Grid:
    """The day and week views: the days on a shared hour axis."""
    days: list[Day]
    lo: int  # first hour shown
    hi: int  # the hour the grid ends
    today: date
    now_top: float | None = None  # the « now » line in today's column (% of the height)

    @property
    def hours(self) -> list[int]:
        return list(range(self.lo, self.hi))

    @property
    def has_strip(self) -> bool:
        return any(d.strip for d in self.days)


def build_grid(days: list[Day], tz, now: datetime | None = None) -> Grid:
    lo, hi = hour_bounds([s for d in days for s in d.timed])
    for d in days:
        layout(d.timed, lo * 60, hi * 60)
    local = (now or utcnow()).astimezone(tz)
    minutes = local.hour * 60 + local.minute
    now_top = None
    if any(d.date == local.date() for d in days) and lo * 60 <= minutes < hi * 60:
        now_top = (minutes - lo * 60) / ((hi - lo) * 60) * 100
    return Grid(days, lo, hi, local.date(), now_top)
