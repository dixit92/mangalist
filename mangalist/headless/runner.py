"""``python -m mangalist --headless``: the scheduler without a window (no Qt import).

    python -m mangalist --headless              run the scheduler until SIGTERM / SIGINT
    python -m mangalist --headless --status     print each job's schedule, last and next run
    python -m mangalist --headless --run rescan run one job now and exit (keeps its schedule)
    python -m mangalist --headless --once       run whatever is due (incl. missed runs) and exit

Logs go to ``headless.log`` in the normal log folder (a separate file from the GUI's
``mangalist.log``, so the two processes in the container never rotate the same file) and, from
INFO up, to standard output (the container log). The file's level, the schedules and the log rotation are
the owner's settings (Settings > Logging / Automation, stored in the database): the runner re-reads them about
once a minute, so a change needs no restart; ``MANGALIST_*_SCHEDULE`` / ``MANGALIST_LOG_LEVEL`` only seed them.
"""

from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
import threading
from pathlib import Path
from typing import List, Optional

_log = logging.getLogger("mangalist.headless")

LOG_NAME = "headless.log"
LOCK_NAME = "headless.lock"


def setup_logging() -> None:
    """The app's logging (``log_config.setup``) for a long-running service: its own file (``headless.log``) and
    INFO and up on standard output. The file's level is the one in Settings > Logging; it is re-read by every
    scheduler tick (``log_config.apply_stored``), so a change needs no restart."""
    from .. import log_config

    log_config.setup(filename=LOG_NAME, console_level=logging.INFO, console_stream=sys.stdout)


class InstanceLock:
    """An exclusive lock on ``headless.lock`` in the data folder: one runner per data folder."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self._fh = None

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fh = open(self.path, "a+", encoding="utf-8")
        try:
            if os.name == "nt":
                import msvcrt

                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            fh.close()
            return False
        fh.seek(0)
        fh.truncate()
        fh.write(f"{os.getpid()}\n")
        fh.flush()
        self._fh = fh
        return True

    def release(self) -> None:
        if self._fh is None:
            return
        try:
            if os.name == "nt":
                import msvcrt

                self._fh.seek(0)
                msvcrt.locking(self._fh.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        self._fh.close()
        self._fh = None


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m mangalist --headless",
                                description="MangaList headless runner (scheduled batch jobs).")
    p.add_argument("--headless", action="store_true", help=argparse.SUPPRESS)
    g = p.add_mutually_exclusive_group()
    g.add_argument("--status", action="store_true", help="print the jobs and their runs, then exit")
    g.add_argument("--run", metavar="JOB", help="run one job now and exit")
    g.add_argument("--once", action="store_true", help="run the due jobs once and exit")
    return p


def _open_database():
    """The library database (settings: schedules, log level), or None when it cannot be opened: the runner then
    runs on the environment's values rather than not at all."""
    try:
        from .. import store

        db = store.get_store()
        db.get_setting("schedule_rescan")           # opens it now, so a broken file shows up here
        return db
    except Exception:  # noqa: BLE001
        _log.warning("Could not open the settings database; using the environment's values", exc_info=True)
        return None


def main(argv: Optional[List[str]] = None) -> int:
    args = _parser().parse_args(sys.argv[1:] if argv is None else argv)

    from .. import __version__, log_config, paths
    from .jobs import build_registry
    from .scheduler import Scheduler
    from .settings import HeadlessSettings, resolve_schedules
    from .state import STATE_NAME, StateStore

    setup_logging()
    db = _open_database()
    if db is not None:
        log_config.apply_stored(db)
    try:
        settings = HeadlessSettings.from_env(db=db)
    except ValueError as exc:
        _log.error("Invalid headless setting: %s", exc)
        return 2

    registry = build_registry(settings)
    store = StateStore(paths.data_dir() / STATE_NAME).load()
    scheduler = Scheduler(registry, store, settings.tz,
                          reload_schedules=lambda: resolve_schedules(db),
                          on_tick=(lambda: log_config.apply_stored(db)) if db is not None else None)

    if args.status:
        for row in scheduler.status():
            print("{job:<16} {schedule:<14} last {last_run} ({last_status})  next {next_run}"
                  .format(**{k: (v if v not in ("", None) else "-") for k, v in row.items()}))
        return 0

    lock = InstanceLock(paths.data_dir() / LOCK_NAME)
    if not lock.acquire():
        _log.error("Another headless runner is using %s; exiting", paths.data_dir())
        return 3
    try:
        current = log_config.current()
        _log.info("MangaList %s headless runner; data folder %s; time zone %s; downloads %s; log level %s",
                  __version__, paths.data_dir(), getattr(settings.tz, "key", settings.tz),
                  "on" if settings.downloads_enabled else "off (opt-in)", current.level if current else "?")

        def _on_signal(signum, _frame):
            _log.info("Received %s; stopping after the current step",
                      signal.Signals(signum).name)
            # Set the stop event from another thread: the handler runs in the main thread, which
            # may be inside Event.wait() holding the event's (non-reentrant) internal lock.
            threading.Thread(target=scheduler.stop, name="headless-stop", daemon=True).start()

        signal.signal(signal.SIGTERM, _on_signal)
        signal.signal(signal.SIGINT, _on_signal)

        if args.run:
            try:
                job = registry.get(args.run)
            except KeyError as exc:
                _log.error("%s", exc.args[0])
                return 2
            result = scheduler.run_job(job, reschedule=False)
            return 0 if result.status in ("ok", "skipped") else 1
        if args.once:
            scheduler.plan()
            scheduler.run_once()
            return 0
        scheduler.run_forever()
        return 0
    finally:
        lock.release()
