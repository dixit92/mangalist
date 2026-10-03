"""Headless runner settings, from environment variables (the Docker image and the Unraid template
set them; a desktop user can export them too).

=============================  ==================  ====================================================
Variable                       Default             Meaning
=============================  ==================  ====================================================
``MANGA_LIST_RESCAN_SCHEDULE``  ``daily@03:30``     When to rescan the library roots (``off`` to stop).
``MANGA_LIST_DOWNLOADS``        ``0``               Opt-in: allow the batched download dispatch.
``MANGA_LIST_DISPATCH_SCHEDULE`` ``daily@04:30``    When the download batch runs (only with downloads on).
``MANGA_LIST_CATCH_UP``         ``1``               After downtime, run a missed job once (never N times).
``MANGA_LIST_ROOTS``            (empty)             Extra library roots, ``os.pathsep``-separated.
``TZ``                          (system)            Time zone of the daily times.
=============================  ==================  ====================================================

The settings database (another lane) will take these over; the environment then stays as an
override for containers.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime, tzinfo
from typing import Mapping, Optional

from .schedule import Schedule, parse_schedule

_log = logging.getLogger(__name__)

ENV_RESCAN = "MANGA_LIST_RESCAN_SCHEDULE"
ENV_DOWNLOADS = "MANGA_LIST_DOWNLOADS"
ENV_DISPATCH = "MANGA_LIST_DISPATCH_SCHEDULE"
ENV_CATCH_UP = "MANGA_LIST_CATCH_UP"

DEFAULT_RESCAN = "daily@03:30"
DEFAULT_DISPATCH = "daily@04:30"

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

    @classmethod
    def from_env(cls, env: Optional[Mapping[str, str]] = None) -> "HeadlessSettings":
        env = os.environ if env is None else env

        def get(name: str, default: str) -> str:
            value = env.get(name)
            return default if value is None else value

        return cls(
            rescan_schedule=parse_schedule(get(ENV_RESCAN, DEFAULT_RESCAN)),
            downloads_enabled=parse_bool(env.get(ENV_DOWNLOADS), False),
            dispatch_schedule=parse_schedule(get(ENV_DISPATCH, DEFAULT_DISPATCH)),
            catch_up=parse_bool(env.get(ENV_CATCH_UP), True),
            tz=local_timezone(env),
        )
