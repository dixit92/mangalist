"""What Settings > Scheduled tasks shows for each task (no Qt): the schedule in words, the last run, the next run, the
status badge and whether "Run now" can be pressed. The runner's own records (``headless-state.json``) are the truth for
"last" and "next"; where the owner has just changed a schedule and the runner has not re-read it yet, the next run is
worked out here and said to be waiting for the runner.

MangaPixer's Scheduled jobs list (1.32.0) is the pattern: one list, each job with its rhythm, last run (outcome, what it
did) and next run, the server's time zone and clock at the top. Here the "server" is the container's runner.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone, tzinfo
from typing import Any, Dict, List, Mapping, Optional

from ..headless.schedule import UTC, parse_schedule
from ..headless.state import JobState
from ..headless.tasks import TASKS, TaskInfo
from .download_rules import ScheduleEntry, schedule_entries

BADGE_RUN, BADGE_OK, BADGE_MUTED, BADGE_BAD = "run", "ok", "muted", "bad"


def moment_text(when: Optional[datetime], tz: tzinfo, now: datetime) -> str:
    """``14:05`` for today, ``Oct 7 19:21`` for another day, in *tz* (the runner's time zone)."""
    if when is None:
        return ""
    local, today = when.astimezone(tz), now.astimezone(tz).date()
    if local.date() == today:
        return local.strftime("%H:%M")
    return f"{local.strftime('%b')} {local.day} {local.strftime('%H:%M')}"


def clock_text(tz: tzinfo, now: datetime) -> str:
    """The line under the heading: "Times are in the container's time zone: America/Toronto (UTC-04:00), now 14:05."."""
    local = now.astimezone(tz)
    offset = local.utcoffset() or (now - now)
    minutes = int(offset.total_seconds() // 60)
    sign = "+" if minutes >= 0 else "-"
    name = getattr(tz, "key", None) or local.tzname() or "local time"
    return (f"Times are in the container's time zone: {name} (UTC{sign}{abs(minutes) // 60:02d}:{abs(minutes) % 60:02d}), "
            f"now {local.strftime('%H:%M')}.")


def duration_text(seconds: float) -> str:
    seconds = max(0, int(round(seconds)))
    if seconds < 60:
        return f"{seconds} s"
    if seconds < 3600:
        return f"{seconds // 60} min {seconds % 60} s" if seconds % 60 else f"{seconds // 60} min"
    return f"{seconds // 3600} h {seconds % 3600 // 60} min"


@dataclass(frozen=True)
class TaskRow:
    info: TaskInfo
    entry: ScheduleEntry
    badge: str                  # the status pill's text
    badge_kind: str
    last: str                   # "Never run yet." / "Oct 14 03:30 - ok: 5 of 5 roots scanned (took 25 s)"
    last_kind: str              # '' | 'bad' (the last run failed)
    next: str                   # "Oct 16 03:30" / "not scheduled" / ...
    can_run: bool
    run_tip: str                # why Run now is unavailable (or what it does)
    running: bool
    requested: bool

    @property
    def job(self) -> str:
        return self.info.job


def _last_text(st: Optional[JobState], tz: tzinfo, now: datetime) -> tuple:
    if st is None or st.last_run is None:
        return "Never run yet.", ""
    when = moment_text(st.last_run, tz, now)
    status = st.last_status or "?"
    took = ""
    if st.last_finished is not None and st.last_finished >= st.last_run:
        took = f" (took {duration_text((st.last_finished - st.last_run).total_seconds())})"
    message = f": {st.last_message}" if st.last_message else ""
    return f"{when} - {status}{message}{took}", "bad" if status == "error" else ""


def _next_text(entry: ScheduleEntry, st: Optional[JobState], tz: tzinfo, now: datetime, *, runner: bool,
               gated_off: bool) -> str:
    if gated_off:
        return "not available: downloads are off in this installation"
    if not entry.valid:
        return "not scheduled (the schedule is not understood)"
    parsed = parse_schedule(entry.edit_text)
    if parsed is None:
        return "not scheduled"
    if not runner:
        return "waiting for a background runner"
    if st is not None and st.next_run is not None and st.schedule == parsed.describe():
        if st.next_run <= now:
            return "due at the runner's next check"
        return moment_text(st.next_run, tz, now)
    # Changed since the runner planned it (or it never ran): the first slot after now, once the runner has re-read it.
    return f"{moment_text(parsed.next_after(now, tz), tz, now)} (the runner picks the new schedule up within a minute)"


def build_rows(db: Any, env: Optional[Mapping[str, str]], states: Mapping[str, JobState], *, tz: tzinfo,
               now: Optional[datetime] = None, runner: bool, downloads_on: bool,
               requests: Optional[List[str]] = None) -> List[TaskRow]:
    """One row per task, in the page's order. *runner*: a headless runner is up; *downloads_on*: downloads are switched
    on in this installation; *requests*: jobs with a Run now request the runner has not taken yet."""
    now = (now or datetime.now(timezone.utc)).astimezone(UTC)
    entries: Dict[str, ScheduleEntry] = {e.job: e for e in schedule_entries(db, env)}
    pending = set(requests or ())
    rows = []
    for info in TASKS:
        entry = entries[info.job]
        st = states.get(info.job)
        gated_off = info.needs_downloads and not downloads_on
        running = bool(runner and st is not None and st.running_since is not None)
        requested = info.job in pending
        last, last_kind = _last_text(st, tz, now)
        off = entry.valid and parse_schedule(entry.edit_text) is None
        if running:
            badge, kind = "Running now", BADGE_RUN
        elif requested:
            badge, kind = "Run requested", BADGE_MUTED
        elif gated_off:
            badge, kind = "Downloads off", BADGE_MUTED
        elif off:
            badge, kind = "Off", BADGE_MUTED
        elif not entry.valid:
            badge, kind = "Not understood", BADGE_BAD
        elif last_kind == "bad":
            badge, kind = "Last run failed", BADGE_BAD
        else:
            badge, kind = "Scheduled", BADGE_OK
        if not runner:
            can_run, tip = False, "Runs in the container's background runner, which is not running here"
        elif gated_off:
            can_run, tip = False, "Downloads are switched off in this installation"
        elif running:
            can_run, tip = False, "It is running now"
        elif requested:
            can_run, tip = False, "Asked for; the runner starts it within a few seconds"
        elif info.job == "clean-records" and off:
            can_run, tip = False, "Choose Daily, Weekly or Monthly first, or use Clear finished now"
        else:
            can_run, tip = True, "Ask the background runner to run it now; its schedule stays as it is"
        rows.append(TaskRow(info, entry, badge, kind, last, last_kind,
                            _next_text(entry, st, tz, now, runner=runner, gated_off=gated_off),
                            can_run, tip, running, requested))
    return rows
