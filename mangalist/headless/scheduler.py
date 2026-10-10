"""The batch scheduler: runs each active job when its persisted due time arrives.

Semantics:

- **Planning.** A job that was never planned, or whose schedule changed since it was planned, is
  next due at the schedule's first slot after "now" (a new or edited schedule never fires at once).
- **After a run** the next due time is the first slot after the run FINISHED, so a slow run never
  queues a second one behind it.
- **Missed runs (downtime).** If the persisted due time has passed when the runner starts, the job
  runs ONCE right away (``catch_up``, the default) - however many slots were missed - and is then
  planned from that run. With ``catch_up`` off the missed slot is skipped and the job is planned
  from now.
- **Shutdown.** :meth:`Scheduler.stop` (SIGTERM / SIGINT) wakes the loop; a running job is asked
  to stop at its next check and is recorded as ``cancelled`` with its due time unchanged, so the
  next start catches it up once.
- **Edited schedules.** Each loop (at least once a ``max_sleep``) the scheduler asks ``reload_schedules`` for the
  schedules now in force (the owner edits them in Settings, stored in the database) and applies a change to a job at
  once: its next run is the new schedule's first slot after "now", logged at INFO; a job switched off is unscheduled.
  ``on_tick`` runs at the same moment (the runner re-reads the logging settings there). Neither may stop the loop:
  a failure is logged once per distinct message and the current schedules stay.
- **Clock.** The loop sleeps at most ``max_sleep`` seconds at a time and re-reads the clock, so a
  suspended host or a clock change is noticed within that time.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, tzinfo
from typing import Callable, Dict, List, Mapping, Optional

from .jobs import Cancelled, Job, JobContext, JobRegistry, JobResult
from .schedule import UTC, Schedule, is_missed
from .state import JobState, StateStore

_log = logging.getLogger(__name__)


def utc_now() -> datetime:
    return datetime.now(UTC)


class Scheduler:
    def __init__(self, registry: JobRegistry, store: StateStore, tz: tzinfo,
                 now: Callable[[], datetime] = utc_now, max_sleep: float = 60.0,
                 wait: Optional[Callable[[float], bool]] = None,
                 reload_schedules: Optional[Callable[[], Mapping[str, Optional[Schedule]]]] = None,
                 on_tick: Optional[Callable[[], None]] = None):
        self.registry = registry
        self.store = store
        self.tz = tz
        self._now = now
        self.max_sleep = max_sleep
        self._stop = threading.Event()
        self._wait = wait or self._stop.wait
        self.current_job: Optional[str] = None
        self._reload = reload_schedules
        self._on_tick = on_tick
        self._tick_problems: Dict[str, str] = {}

    # --- control ----------------------------------------------------------------------------

    def stop(self) -> None:
        self._stop.set()

    @property
    def stopping(self) -> bool:
        return self._stop.is_set()

    # --- planning ---------------------------------------------------------------------------

    def plan(self) -> Dict[str, datetime]:
        """Fix every active job's due time at startup (incl. missed-run handling); persist it."""
        now = self._now()
        planned: Dict[str, datetime] = {}
        for job in self.registry.active():
            st = self.store.get(job.name)
            desc = job.schedule.describe()  # type: ignore[union-attr]
            if st.next_run is None or st.schedule != desc:
                if st.schedule and st.schedule != desc:
                    _log.info("%s: schedule changed (%s -> %s)", job.name, st.schedule, desc)
                st.next_run = job.schedule.next_after(now, self.tz)  # type: ignore[union-attr]
                st.schedule = desc
            elif is_missed(st.next_run, now):
                if job.catch_up:
                    _log.info("%s: missed its run at %s (runner was down); running once now",
                              job.name, self._fmt(st.next_run))
                else:
                    nxt = job.schedule.next_after(now, self.tz)  # type: ignore[union-attr]
                    _log.info("%s: missed its run at %s; catch-up is off, next at %s",
                              job.name, self._fmt(st.next_run), self._fmt(nxt))
                    st.next_run = nxt
            planned[job.name] = st.next_run  # type: ignore[assignment]
        for job in self.registry.all():
            if not job.active:
                _log.info("%s: not scheduled (%s)", job.name,
                          "disabled" if not job.enabled else "no schedule")
        self.store.save()
        for name, when in planned.items():
            if when <= now:
                _log.info("%s: due now", name)
            else:
                _log.info("%s: next run %s", name, self._fmt(when))
        return planned

    def refresh(self) -> bool:
        """Re-read what the owner can change while the runner is up (schedules, logging); True if a schedule changed."""
        if self._on_tick is not None:
            try:
                self._on_tick()
                self._tick_problems.pop("tick", None)
            except Exception as exc:  # noqa: BLE001 - never stop the runner for this
                self._problem("tick", "Could not refresh the logging settings", exc)
        if self._reload is None:
            return False
        try:
            wanted = self._reload()
            self._tick_problems.pop("schedules", None)
        except Exception as exc:  # noqa: BLE001 - keep the schedules in force
            self._problem("schedules", "Could not re-read the schedules; keeping the current ones", exc)
            return False
        return self.apply_schedules(wanted)

    def _problem(self, key: str, what: str, exc: Exception) -> None:
        text = f"{type(exc).__name__}: {exc}"
        if self._tick_problems.get(key) != text:             # once per distinct problem, not once a minute
            self._tick_problems[key] = text
            _log.warning("%s (%s)", what, text, exc_info=True)

    def apply_schedules(self, wanted: Mapping[str, Optional[Schedule]]) -> bool:
        """Switch the jobs named in *wanted* to their new schedules; a changed job's next run is the first slot after
        now (an edited schedule never fires at once). Persists and logs each change."""
        now = self._now()
        changed = False
        for job in self.registry.all():
            if job.name not in wanted or wanted[job.name] == job.schedule:
                continue
            new = wanted[job.name]
            was = job.schedule.describe() if job.schedule else "off"
            job.set_schedule(new)
            st = self.store.get(job.name)
            changed = True
            if job.active:
                st.next_run = new.next_after(now, self.tz)  # type: ignore[union-attr]
                st.schedule = new.describe()                # type: ignore[union-attr]
                _log.info("%s: schedule changed (%s -> %s); next run %s", job.name, was, new.describe(),  # type: ignore[union-attr]
                          self._fmt(st.next_run))
            else:
                st.next_run = None
                st.schedule = ""
                _log.info("%s: schedule changed (%s -> %s); not scheduled (%s)", job.name, was,
                          new.describe() if new else "off", "disabled" if not job.enabled and new else "no schedule")
        if changed:
            self.store.save()
        return changed

    def due_jobs(self, now: Optional[datetime] = None) -> List[Job]:
        now = now or self._now()
        due = [(self.store.get(j.name).next_run, j) for j in self.registry.active()]
        return [j for when, j in sorted((d for d in due if d[0] is not None and d[0] <= now),
                                        key=lambda d: d[0])]

    def seconds_until_next(self, now: Optional[datetime] = None) -> float:
        now = now or self._now()
        times = [self.store.get(j.name).next_run for j in self.registry.active()]
        times = [t for t in times if t is not None]
        if not times:
            return self.max_sleep
        return max(0.0, min(self.max_sleep, (min(times) - now).total_seconds()))

    # --- running ----------------------------------------------------------------------------

    def run_job(self, job: Job, reschedule: bool = True) -> JobResult:
        st: JobState = self.store.get(job.name)
        started = self._now()
        st.last_run = started
        self.current_job = job.name
        _log.info("%s: starting", job.name)
        try:
            result = job.func(JobContext(lambda: self._stop.is_set()))
            if not isinstance(result, JobResult):
                result = JobResult()
        except Cancelled:
            result = JobResult("cancelled", "stopped by shutdown")
        except Exception as exc:  # noqa: BLE001 - a failing job must not stop the runner
            _log.exception("%s: failed", job.name)
            result = JobResult("error", str(exc) or type(exc).__name__)
        finally:
            self.current_job = None
        finished = self._now()
        st.last_finished = finished
        st.last_status = result.status
        st.last_message = result.message
        st.runs += 1
        st.extra = dict(result.extra)
        if reschedule and job.schedule is not None and result.status != "cancelled":
            st.next_run = job.schedule.next_after(finished, self.tz)
            st.schedule = job.schedule.describe()
        self.store.save()
        took = (finished - started).total_seconds()
        _log.log(logging.ERROR if result.status == "error" else logging.INFO, "%s: %s in %.1f s%s%s",
                 job.name, result.status, took, f" - {result.message}" if result.message else "",
                 f"; next run {self._fmt(st.next_run)}" if reschedule and st.next_run else "")
        return result

    def run_once(self) -> int:
        """Run every job that is due now; return how many ran."""
        ran = 0
        for job in self.due_jobs():
            if self.stopping:
                break
            self.run_job(job)
            ran += 1
        return ran

    def run_forever(self) -> None:
        self.plan()
        while not self.stopping:
            self.refresh()
            self.run_once()
            if self.stopping:
                break
            self._wait(self.seconds_until_next())
        _log.info("Scheduler stopped")

    # --- reporting --------------------------------------------------------------------------

    def status(self) -> List[Dict[str, object]]:
        rows = []
        for job in self.registry.all():
            st = self.store.get(job.name)
            rows.append({
                "job": job.name,
                "active": job.active,
                "schedule": job.schedule.describe() if job.schedule else "off",
                "enabled": job.enabled,
                "last_run": self._fmt(st.last_run),
                "last_status": st.last_status or "",
                "next_run": self._fmt(st.next_run) if job.active else "",
            })
        return rows

    def _fmt(self, dt: Optional[datetime]) -> str:
        if dt is None:
            return "-"
        local = dt.astimezone(self.tz)
        return local.strftime("%Y-%m-%d %H:%M %Z").strip()


__all__ = ["Scheduler", "utc_now"]
