"""Headless runner settings, from environment variables (the Docker image and the Unraid template
set them; a desktop user can export them too).

=============================  ==================  ====================================================
Variable                       Default             Meaning
=============================  ==================  ====================================================
``MANGALIST_RESCAN_SCHEDULE``  ``daily@03:30``     When to rescan the library roots (``off`` to stop).
``MANGALIST_DOWNLOADS``        ``0``               Opt-in: downloads (the volumes MVP; the Unraid container).
``MANGALIST_DISPATCH_SCHEDULE`` ``daily@04:30``    When the download batch runs (only with downloads on).
``MANGALIST_DOWNLOADS_SCHEDULE`` ``every 1h``      When to file finished downloads and remove completed
                                                   torrents (the ``downloads`` job; only with downloads on).
``MANGALIST_CATCH_UP``         ``1``               After downtime, run a missed job once (never N times).
``MANGALIST_MANGAPIXER_SYNC_SCHEDULE`` ``daily@03:15`` When to sync the MangaPixer source (before the rescan;
                                                   skipped while no MangaPixer server is set up).
``MANGALIST_ROOTS``            (empty)             Extra library roots, ``os.pathsep``-separated.
``TZ``                          (system)            Time zone of the daily times.
=============================  ==================  ====================================================

Schedules in the settings database (owner, 2026-10-09: "This should be configurable by the user")
--------------------------------------------------------------------------------------------------
The four schedules are editable in Settings > Automation and stored in the library database's settings table
(``schedule_rescan``, ``schedule_mangapixer_sync``, ``schedule_dispatch``, ``schedule_downloads``; the text forms of
:mod:`.schedule`, ``off`` included). Order of precedence, per schedule:

1. the stored value, once the owner has set one;
2. else the environment variable above (the container template's value);
3. else the built-in default.

So the environment variable only SEEDS the schedule - it is the first-run default - and never overrides what the owner
chose in Settings. Why not the other way round (an explicit variable wins, its field greyed out): the Docker image
bakes every one of these variables in (``ENV MANGALIST_RESCAN_SCHEDULE=daily@03:30`` ...), so in the container every
variable is "explicitly set" and nothing would ever be editable. "Use the container's value" in Settings forgets the
stored value, so a variable changed in the container template applies again. The runner re-reads the schedules every
scheduler tick (:meth:`~mangalist.headless.scheduler.Scheduler.run_forever`), so a change needs no restart.
``MANGALIST_DOWNLOADS`` (downloads on/off) and ``MANGALIST_CATCH_UP`` stay environment-only.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime, tzinfo
from typing import Any, Dict, Mapping, NamedTuple, Optional

from .schedule import Schedule, parse_schedule, validate_schedule

_log = logging.getLogger(__name__)

ENV_RESCAN = "MANGALIST_RESCAN_SCHEDULE"
ENV_DOWNLOADS = "MANGALIST_DOWNLOADS"
ENV_DISPATCH = "MANGALIST_DISPATCH_SCHEDULE"
ENV_CATCH_UP = "MANGALIST_CATCH_UP"
ENV_MANGAPIXER = "MANGALIST_MANGAPIXER_SYNC_SCHEDULE"
ENV_DOWNLOADS_SCHEDULE = "MANGALIST_DOWNLOADS_SCHEDULE"

DEFAULT_RESCAN = "daily@03:30"
DEFAULT_DISPATCH = "daily@04:30"
DEFAULT_MANGAPIXER = "daily@03:15"
DEFAULT_DOWNLOADS_SCHEDULE = "every 1h"


class ScheduleSpec(NamedTuple):
    """One editable schedule: the job it belongs to, where it is stored, its environment variable and default."""

    job: str            # the job's name in the registry
    label: str          # what Settings calls it
    key: str            # the settings-table key
    env: str
    default: str


SCHEDULES = (
    ScheduleSpec("rescan", "Rescan the library", "schedule_rescan", ENV_RESCAN, DEFAULT_RESCAN),
    ScheduleSpec("mangapixer-sync", "Sync with MangaPixer", "schedule_mangapixer_sync", ENV_MANGAPIXER,
                 DEFAULT_MANGAPIXER),
    ScheduleSpec("dispatch-batch", "Dispatch the batched downloads", "schedule_dispatch", ENV_DISPATCH,
                 DEFAULT_DISPATCH),
    ScheduleSpec("downloads", "File finished downloads", "schedule_downloads", ENV_DOWNLOADS_SCHEDULE,
                 DEFAULT_DOWNLOADS_SCHEDULE),
)
SCHEDULE_BY_JOB = {spec.job: spec for spec in SCHEDULES}

SOURCE_STORED = "stored"        # set in Settings
SOURCE_ENV = "env"              # the container's variable (first-run default)
SOURCE_DEFAULT = "default"      # nothing set anywhere: the built-in default


class ScheduleChoice(NamedTuple):
    text: str           # canonical-ish text (``daily@03:30``, ``every 1h``, ``off``) or the raw text of a bad variable
    source: str         # SOURCE_*


def resolve_schedule(spec: ScheduleSpec, db: Any = None, env: Optional[Mapping[str, str]] = None) -> ScheduleChoice:
    """The text of *spec*'s schedule and where it comes from: the stored value, else the environment, else the
    default. A stored value that does not parse (hand-edited database) is ignored with a warning. The text of a bad
    ENVIRONMENT value is returned as it is (:meth:`HeadlessSettings.from_env` refuses it; Settings shows it flagged)."""
    env = os.environ if env is None else env
    if db is not None:
        try:
            stored = db.get_setting(spec.key)
        except Exception:  # noqa: BLE001 - an unreadable database must not stop the runner
            _log.warning("Could not read the %s schedule from the database; using the container's value", spec.job,
                         exc_info=True)
            stored = None
        if isinstance(stored, str):
            try:
                parse_schedule(stored)
            except ValueError:
                _log.warning("Ignoring the stored %s schedule %r: not a schedule", spec.job, stored)
            else:
                return ScheduleChoice(stored.strip(), SOURCE_STORED)
    raw = env.get(spec.env)
    if raw is not None:
        return ScheduleChoice(raw, SOURCE_ENV)
    return ScheduleChoice(spec.default, SOURCE_DEFAULT)


def resolve_schedules(db: Any = None, env: Optional[Mapping[str, str]] = None) -> Dict[str, Optional[Schedule]]:
    """job name -> its schedule now (None = off). A bad value raises ValueError (naming the variable)."""
    out: Dict[str, Optional[Schedule]] = {}
    for spec in SCHEDULES:
        choice = resolve_schedule(spec, db, env)
        try:
            out[spec.job] = parse_schedule(choice.text)
        except ValueError as exc:
            raise ValueError(f"{spec.env}: {exc}") from exc
    return out


def save_schedule(db: Any, spec: ScheduleSpec, text: str) -> str:
    """Validate *text* (ValueError with a message for the user) and store it; returns the stored text."""
    value = validate_schedule(text)
    db.set_setting(spec.key, value)
    _log.info("Schedule %s set to %s", spec.job, value)
    return value


def reset_schedule(db: Any, spec: ScheduleSpec) -> None:
    """Forget the stored value: the container's variable (or the default) applies again."""
    db.delete_setting(spec.key)
    _log.info("Schedule %s: back to the container's value", spec.job)


_TRUE = {"1", "true", "yes", "y", "on", "enable", "enabled"}
_FALSE = {"0", "false", "no", "n", "off", "disable", "disabled", ""}


def parse_bool(value: Optional[str], default: bool) -> bool:
    if value is None:
        return default
    v = value.strip().lower()
    if v in _TRUE:
        return True
    if v in _FALSE:
        return False
    raise ValueError(f"expected a yes/no value, got {value!r}")


def local_timezone(env: Optional[Mapping[str, str]] = None) -> tzinfo:
    """``TZ`` as an IANA zone when it names one, else the system's current local zone."""
    env = os.environ if env is None else env
    name = (env.get("TZ") or "").strip().lstrip(":")
    if name:
        try:
            from zoneinfo import ZoneInfo

            return ZoneInfo(name)
        except Exception:  # noqa: BLE001 - ZoneInfoNotFoundError, ValueError, missing tzdata
            _log.warning("Unknown time zone TZ=%r; using the system's local time", name)
    zone = _system_zone_name()
    if zone:
        try:
            from zoneinfo import ZoneInfo

            return ZoneInfo(zone)
        except Exception:  # noqa: BLE001
            pass
    # A fixed offset: correct today; a DST change shifts daily runs by an hour until restart.
    return datetime.now().astimezone().tzinfo  # type: ignore[return-value]


def _system_zone_name() -> str:
    """The IANA name behind ``/etc/localtime`` (Linux, macOS), or ''."""
    try:
        target = os.path.realpath("/etc/localtime")
    except OSError:
        return ""
    marker = "zoneinfo" + os.sep
    return target.split(marker, 1)[1] if marker in target else ""


@dataclass(frozen=True)
class HeadlessSettings:
    rescan_schedule: Optional[Schedule]
    downloads_enabled: bool
    dispatch_schedule: Optional[Schedule]
    catch_up: bool
    tz: tzinfo
    mangapixer_schedule: Optional[Schedule] = None
    downloads_schedule: Optional[Schedule] = None

    @classmethod
    def from_env(cls, env: Optional[Mapping[str, str]] = None, db: Any = None) -> "HeadlessSettings":
        """The settings from the environment, with the schedules the owner stored in *db* (if given) in front of
        the environment's (see the module docstring). ValueError for a bad schedule or yes/no value."""
        env = os.environ if env is None else env
        schedules = resolve_schedules(db, env)
        return cls(
            rescan_schedule=schedules["rescan"],
            downloads_enabled=parse_bool(env.get(ENV_DOWNLOADS), False),
            dispatch_schedule=schedules["dispatch-batch"],
            catch_up=parse_bool(env.get(ENV_CATCH_UP), True),
            tz=local_timezone(env),
            mangapixer_schedule=schedules["mangapixer-sync"],
            downloads_schedule=schedules["downloads"],
        )
