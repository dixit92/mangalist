"""The scheduled tasks as Settings > Scheduled tasks shows them (owner, 2026-10-15: "See MangaPixer pattern to combine
scheduled tasks"): one list - each task, what it does, when it runs, its last run and its next run, and "Run now".

MangaPixer's Scheduled jobs section is one list of cards that read the server's own job records; this is the same for
MangaList's headless runner. The runner is another process (in the Docker / Unraid container), so the page

- reads the runner's own records (``headless-state.json``: last run, outcome, message, next run, running now) without
  changing them (:func:`read_states`), and whether a runner is running at all (:func:`runner_running`);
- asks for "Run now" by leaving a request in the database's ``meta`` table (:func:`request_run`); the runner takes it
  within a few seconds (:func:`take_requests`), runs the job once and keeps its schedule. The page never runs a batch
  job itself - two processes scanning at once is what the scan lock and this indirection avoid.

No Qt here.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .state import STATE_NAME, JobState

_log = logging.getLogger(__name__)

META_PREFIX = "headless.run_request."


@dataclass(frozen=True)
class TaskInfo:
    job: str            # the job's name in the runner's registry
    title: str          # what the page calls it
    what: str           # one plain sentence on what it does
    needs_downloads: bool = False


#: The page's order: the library first, then the download chores, the clean-up last.
TASKS = (
    TaskInfo("mangapixer-sync", "Sync with MangaPixer",
             "Fetches MangaPixer's library and volume data, so the rescan after it sees the latest links."),
    TaskInfo("rescan", "Full re-read of the library",
             "Reads every library folder again from disk and records what changed. The Rescan button does the same "
             "when you ask."),
    TaskInfo("downloads", "Check downloads",
             "Files finished downloads into their series folders, removes completed torrents and hands queued torrents "
             "over while they fit the download budget.", needs_downloads=True),
    TaskInfo("dispatch-batch", "Find missing volumes and chapters",
             "Searches for series with gaps; series on Auto get what is found sent, the rest waits under Found.",
             needs_downloads=True),
    TaskInfo("chapter-check", "Check for new chapters",
             "Asks the saved source of each ongoing chapter series for chapters newer than the newest one you have, "
             "before MangaUpdates lists them; series on Auto get them sent, the rest waits under Found.",
             needs_downloads=True),
    TaskInfo("extension-updates", "Check for extension updates",
             "Asks the built-in extension host which installed extensions have a newer version in their repository; "
             "the Download tab and Connected services say so, and with Auto-update on (Built-in extensions > Edit) "
             "they are installed. Runs only while Built-in extensions is chosen."),
    TaskInfo("clean-records", "Clear finished download records",
             "Removes finished entries from the Download tab's Show finished list once they are older than the "
             "period. Nothing in progress, filed or failed is touched."),
)
TASK_BY_JOB = {t.job: t for t in TASKS}


def state_path() -> Path:
    from .. import paths

    return paths.data_dir() / STATE_NAME


def read_states(path: Optional[Path] = None) -> Dict[str, JobState]:
    """The runner's job records, read only (an unreadable or missing file is no records; nothing is renamed aside the
    way the runner's own :class:`~mangalist.headless.state.StateStore` does)."""
    path = Path(path) if path is not None else state_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        jobs = data.get("jobs")
        if not isinstance(jobs, dict):
            return {}
    except (OSError, ValueError, AttributeError):
        return {}
    return {name: JobState.from_json(d) for name, d in jobs.items() if isinstance(name, str) and isinstance(d, dict)}


def runner_running(lock_path: Optional[Path] = None) -> bool:
    """True while a headless runner holds the data folder's instance lock (a runner is up, here or in the container
    sharing the folder). Looks only: the lock is taken and given back at once, nothing is written."""
    import os

    from .runner import LOCK_NAME

    if lock_path is None:
        from .. import paths

        lock_path = paths.data_dir() / LOCK_NAME
    lock_path = Path(lock_path)
    if not lock_path.exists():
        return False
    try:
        fh = open(lock_path, "r+b")
    except OSError:
        return False
    try:
        if os.name == "nt":
            import msvcrt

            try:
                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                return True
            msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
            return False
        import fcntl

        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return True
        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        return False
    finally:
        fh.close()


# --- Run now ---------------------------------------------------------------------------------------------------------

def request_run(db: Any, job: str) -> None:
    """Leave a "run this job now" request for the runner."""
    if job not in TASK_BY_JOB:
        raise ValueError(f"unknown task {job!r}")
    db.set_meta(META_PREFIX + job, datetime.now(timezone.utc).replace(microsecond=0).isoformat())


def pending_requests(db: Any) -> List[str]:
    """The jobs with a request waiting, in the page's order."""
    with db.connect() as con:
        waiting = {r[0][len(META_PREFIX):] for r in con.execute(
            "SELECT key FROM meta WHERE key LIKE ? AND value IS NOT NULL", (META_PREFIX + "%",))}
    return [t.job for t in TASKS if t.job in waiting] + sorted(waiting - set(TASK_BY_JOB))


def take_requests(db: Any) -> List[str]:
    """The jobs with a request waiting, and forget the requests (the runner calls this; each request runs once)."""
    with db.connect() as con:
        rows = con.execute("SELECT key FROM meta WHERE key LIKE ?", (META_PREFIX + "%",)).fetchall()
        if not rows:
            return []
        con.execute("DELETE FROM meta WHERE key LIKE ?", (META_PREFIX + "%",))
    waiting = {r[0][len(META_PREFIX):] for r in rows}
    return [t.job for t in TASKS if t.job in waiting] + sorted(waiting - set(TASK_BY_JOB))
