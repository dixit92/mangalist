"""Batch windows for the headless runner: "weekly on a day at HH:MM", "daily at HH:MM" and "every N hours".

All instants are timezone-aware. Persisted times are UTC; daily times are wall-clock times in the
runner's time zone (the container's ``TZ``), so a daily 03:30 job stays at 03:30 local time across
daylight-saving changes:

- spring forward (03:30 does not exist that night, e.g. 02:30 in a 02:00 -> 03:00 gap): the job runs
  at the first instant after the gap that the same offset arithmetic gives (02:30 -> 03:30);
- fall back (the time exists twice): the job runs once, at the first occurrence.

"Every N hours" counts elapsed time (UTC), so it is not affected by daylight saving at all.

Text forms (environment variables, settings):

    weekly@sun 03:30   weekly sunday 03:30  -> WeeklyAt(6, 3, 30)    (days: mon ... sun, Monday = 0)
    daily@03:30   daily 03:30   03:30      -> DailyAt(3, 30)
    every 12h     every:12h     12h        -> EveryHours(12)
    off           none          disabled   -> None (job not scheduled)
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone, tzinfo
from typing import Optional, Union

UTC = timezone.utc


def _require_aware(dt: datetime, name: str = "after") -> None:
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")


def resolve_local(day: date, at: time, tz: tzinfo) -> datetime:
    """The UTC instant of wall-clock ``day`` + ``at`` in ``tz``.

    Ambiguous times (fall back) resolve to the first occurrence; non-existent times (spring
    forward) resolve forward past the gap.
    """
    local = datetime.combine(day, at).replace(tzinfo=tz, fold=0)
    # In a gap, fold=0 applies the offset in force BEFORE the transition, which lands on the
    # wall-clock time just after the gap (02:30 -> 03:30 for a one-hour gap): what we want.
    return local.astimezone(UTC)


@dataclass(frozen=True)
class DailyAt:
    """Once a day at ``hour:minute`` local (wall-clock) time."""

    hour: int
    minute: int = 0

    def __post_init__(self) -> None:
        if not (0 <= self.hour <= 23 and 0 <= self.minute <= 59):
            raise ValueError(f"invalid time of day {self.hour}:{self.minute}")

    def next_after(self, after: datetime, tz: tzinfo) -> datetime:
        """The first run strictly after ``after`` (UTC)."""
        _require_aware(after)
        at = time(self.hour, self.minute)
        day = after.astimezone(tz).date() - timedelta(days=1)
        # Yesterday's slot can still be after ``after`` around a DST change; try three days.
        for _ in range(4):
            candidate = resolve_local(day, at, tz)
            if candidate > after:
                return candidate
            day += timedelta(days=1)
        raise AssertionError("unreachable: a daily slot exists within three days")

    def describe(self) -> str:
        return f"daily@{self.hour:02d}:{self.minute:02d}"


WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


@dataclass(frozen=True)
class WeeklyAt:
    """Once a week, on ``weekday`` (Monday = 0) at ``hour:minute`` local (wall-clock) time (owner, 2026-10-10: the
    schedule choices are weekly, daily, every 12 hours and every 6 hours)."""

    weekday: int
    hour: int
    minute: int = 0

    def __post_init__(self) -> None:
        if not (0 <= self.weekday <= 6):
            raise ValueError(f"invalid weekday {self.weekday}")
        if not (0 <= self.hour <= 23 and 0 <= self.minute <= 59):
            raise ValueError(f"invalid time of day {self.hour}:{self.minute}")

    def next_after(self, after: datetime, tz: tzinfo) -> datetime:
        """The first run strictly after ``after`` (UTC)."""
        _require_aware(after)
        at = time(self.hour, self.minute)
        day = after.astimezone(tz).date() - timedelta(days=1)
        for _ in range(10):                 # the weekday comes within eight days; one spare around a DST change
            if day.weekday() == self.weekday:
                candidate = resolve_local(day, at, tz)
                if candidate > after:
                    return candidate
            day += timedelta(days=1)
        raise AssertionError("unreachable: a weekly slot exists within nine days")

    def describe(self) -> str:
        return f"weekly@{WEEKDAYS[self.weekday]} {self.hour:02d}:{self.minute:02d}"


@dataclass(frozen=True)
class EveryHours:
    """Every ``hours`` hours of elapsed time, counted from the previous run."""

    hours: float

    def __post_init__(self) -> None:
        if not (self.hours > 0):
            raise ValueError("interval must be positive")

    @property
    def interval(self) -> timedelta:
        return timedelta(hours=self.hours)

    def next_after(self, after: datetime, tz: tzinfo) -> datetime:  # noqa: ARG002 - same interface
        _require_aware(after)
        return after.astimezone(UTC) + self.interval

    def describe(self) -> str:
        h = int(self.hours) if float(self.hours).is_integer() else self.hours
        return f"every {h}h"


Schedule = Union[WeeklyAt, DailyAt, EveryHours]

_OFF = {"", "off", "none", "never", "disabled", "disable", "0", "false", "no"}
_WEEKLY = re.compile(r"^weekly\s*[@: ]\s*(mon|tue|wed|thu|fri|sat|sun)[a-z]*\s+(\d{1,2}):(\d{2})$")
_DAILY = re.compile(r"^(?:daily\s*[@: ]\s*)?(\d{1,2}):(\d{2})$")
_EVERY = re.compile(r"^(?:every\s*[: ]?\s*)?(\d+(?:\.\d+)?)\s*(h|hr|hrs|hour|hours)$")


def parse_schedule(text: Optional[str]) -> Optional[Schedule]:
    """Parse a schedule string (see the module docstring). ``None`` means "not scheduled".

    Raises ValueError for anything else, so a typo in a container variable is reported instead of
    silently running at a surprising time.
    """
    value = (text or "").strip().lower()
    if value in _OFF:
        return None
    m = _WEEKLY.match(value)
    if m:
        return WeeklyAt(WEEKDAYS.index(m.group(1)), int(m.group(2)), int(m.group(3)))
    m = _DAILY.match(value)
    if m:
        return DailyAt(int(m.group(1)), int(m.group(2)))
    m = _EVERY.match(value)
    if m:
        return EveryHours(float(m.group(1)))
    raise ValueError(
        f"unrecognised schedule {text!r}; use e.g. 'weekly@sun 03:30', 'daily@03:30', 'every 12h' or 'off'")


MIN_EVERY_HOURS = 0.25      # the settings refuse "every 1m": a rescan or a MangaPixer sync that often helps nobody


def validate_schedule(text: Optional[str]) -> str:
    """The text a user typed in Settings > Automation, checked and rewritten in the canonical form for storing
    (``weekly@sun 03:30``, ``daily@03:30``, ``every 12h`` or ``off``).

    Accepts everything :func:`parse_schedule` does (so ``03:30``, ``daily 3:30``, ``12h`` work); raises ValueError with
    a message to show as it is. An empty entry is refused (it is easy to clear a field by accident: write ``off``).
    """
    value = (text or "").strip()
    if not value:
        raise ValueError("Enter a time such as daily@03:30 or weekly@sun 03:30, an interval such as every 12h, or off "
                         "to stop it.")
    try:
        parsed = parse_schedule(value)
    except ValueError as exc:
        raise ValueError(_BAD_SCHEDULE) from exc
    if parsed is None:
        return "off"
    if isinstance(parsed, EveryHours) and parsed.hours < MIN_EVERY_HOURS:
        raise ValueError("That is too often: the shortest interval is every 15 minutes (every 0.25h).")
    return parsed.describe()


_BAD_SCHEDULE = ("Not understood. Write weekly@sun 03:30 (Sundays at 03:30), daily@03:30 (every day at 03:30, 24-hour "
                 "clock), every 12h (every 12 hours) or off.")


def is_missed(next_run: Optional[datetime], now: datetime) -> bool:
    """A persisted due time that has already passed when the runner (re)starts."""
    return next_run is not None and next_run <= now
