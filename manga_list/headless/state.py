"""Persisted scheduler state: last / next run per job, in a small JSON file in the data folder.

Phase 0 keeps this in ``headless-state.json`` (another lane owns the database). The file is
rewritten atomically (temporary file + ``os.replace``) so a crash or ``SIGKILL`` never leaves half
a file; an unreadable file is renamed aside and the runner starts from empty state (every job is
then planned from "now", which never causes a burst of runs).
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from .schedule import UTC

_log = logging.getLogger(__name__)

STATE_NAME = "headless-state.json"
STATE_VERSION = 1


def _iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.astimezone(UTC).isoformat(timespec="seconds") if dt is not None else None


def _parse(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


@dataclass
class JobState:
    last_run: Optional[datetime] = None        # start of the last run
    last_finished: Optional[datetime] = None
    last_status: Optional[str] = None          # ok | error | skipped | cancelled
    last_message: str = ""
    next_run: Optional[datetime] = None
    schedule: str = ""                         # describe() of the schedule next_run was planned with
    runs: int = 0
    extra: Dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> Dict[str, Any]:
        d = asdict(self)
        for key in ("last_run", "last_finished", "next_run"):
            d[key] = _iso(getattr(self, key))
        return d

    @classmethod
    def from_json(cls, d: Dict[str, Any]) -> "JobState":
        extra = d.get("extra")
        try:
            runs = int(d.get("runs") or 0)
        except (TypeError, ValueError):
            runs = 0
        return cls(
            last_run=_parse(d.get("last_run")),
            last_finished=_parse(d.get("last_finished")),
            last_status=d.get("last_status") if isinstance(d.get("last_status"), str) else None,
            last_message=str(d.get("last_message") or ""),
            next_run=_parse(d.get("next_run")),
            schedule=str(d.get("schedule") or ""),
            runs=runs,
            extra=extra if isinstance(extra, dict) else {},
        )


class StateStore:
    """Load / save :class:`JobState` per job name."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.jobs: Dict[str, JobState] = {}

    def load(self) -> "StateStore":
        self.jobs = {}
        if not self.path.exists():
            return self
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            jobs = data.get("jobs") if isinstance(data, dict) else None
            if not isinstance(jobs, dict):
                raise ValueError("no 'jobs' object")
        except (OSError, ValueError) as exc:
            aside = self.path.with_name(self.path.name + ".unreadable")
            _log.warning("Scheduler state %s is unreadable (%s); starting fresh, kept as %s",
                         self.path, exc, aside.name)
            try:
                os.replace(self.path, aside)
            except OSError:
                pass
            return self
        for name, d in jobs.items():
            if isinstance(name, str) and isinstance(d, dict):
                self.jobs[name] = JobState.from_json(d)
        return self

    def get(self, name: str) -> JobState:
        return self.jobs.setdefault(name, JobState())

    def save(self) -> None:
        payload = {"version": STATE_VERSION,
                   "jobs": {name: st.to_json() for name, st in sorted(self.jobs.items())}}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=self.path.name + ".", suffix=".tmp", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2, ensure_ascii=False)
                f.write("\n")
            os.replace(tmp, self.path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
