"""Headless runner: scheduled batch jobs without a window (``python -m manga_list --headless``).

Qt-free by design - nothing in this package imports PySide6, so the runner works in a container
service, on a server without a display, and in the no-Qt test job.
"""

from .jobs import (ConfigRootsProvider, Job, JobContext, JobRegistry, JobResult, RootsProvider,
                   StoreRootsProvider)
from .schedule import DailyAt, EveryHours, parse_schedule
from .scheduler import Scheduler
from .settings import HeadlessSettings
from .state import JobState, StateStore

__all__ = [
    "ConfigRootsProvider", "DailyAt", "EveryHours", "HeadlessSettings", "Job", "JobContext",
    "JobRegistry", "JobResult", "JobState", "RootsProvider", "Scheduler", "StateStore", "StoreRootsProvider",
    "parse_schedule",
]
