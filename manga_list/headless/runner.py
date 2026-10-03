"""``python -m manga_list --headless``: the scheduler without a window (no Qt import).

    python -m manga_list --headless              run the scheduler until SIGTERM / SIGINT
    python -m manga_list --headless --status     print each job's schedule, last and next run
    python -m manga_list --headless --run rescan run one job now and exit (keeps its schedule)
    python -m manga_list --headless --once       run whatever is due (incl. missed runs) and exit

Logs go to ``headless.log`` in the normal log folder (a separate file from the GUI's
``manga_list.log``, so the two processes in the container never rotate the same file) and, from
INFO up, to standard output (the container log).
"""

from __future__ import annotations

import argparse
import logging
import logging.handlers
import os
import signal
import sys
import threading
from pathlib import Path
from typing import List, Optional

_log = logging.getLogger("manga_list.headless")

LOG_NAME = "headless.log"
LOCK_NAME = "headless.lock"


def setup_logging() -> None:
    """The app's logging (``log_config.setup``), retargeted for a long-running service."""
    from .. import log_config, paths

    log_config.setup()
    root = logging.getLogger()
    for h in list(root.handlers):
        if isinstance(h, logging.handlers.RotatingFileHandler) \
                and Path(h.baseFilename).name == "manga_list.log":
            root.removeHandler(h)
            h.close()
            fh = logging.handlers.RotatingFileHandler(
                paths.log_dir() / LOG_NAME, maxBytes=h.maxBytes, backupCount=h.backupCount,
                encoding="utf-8", delay=True)
            fh.setLevel(h.level)
            fh.setFormatter(h.formatter)
            root.addHandler(fh)
        elif type(h) is logging.StreamHandler:
            h.setStream(sys.stdout)
            h.setLevel(logging.INFO)
    # Libraries that log every HTTP request stay at WARNING on the console.
    logging.getLogger("urllib3").setLevel(logging.WARNING)


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
    p = argparse.ArgumentParser(prog="python -m manga_list --headless",
                                description="MangaList headless runner (scheduled batch jobs).")
    p.add_argument("--headless", action="store_true", help=argparse.SUPPRESS)
    g = p.add_mutually_exclusive_group()
    g.add_argument("--status", action="store_true", help="print the jobs and their runs, then exit")
    g.add_argument("--run", metavar="JOB", help="run one job now and exit")
    g.add_argument("--once", action="store_true", help="run the due jobs once and exit")
    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = _parser().parse_args(sys.argv[1:] if argv is None else argv)

    from .. import __version__, paths
    from .jobs import build_registry
    from .scheduler import Scheduler
    from .settings import HeadlessSettings
    from .state import STATE_NAME, StateStore

    setup_logging()
    try:
        settings = HeadlessSettings.from_env()
    except ValueError as exc:
        _log.error("Invalid headless setting: %s", exc)
        return 2

    registry = build_registry(settings)
    store = StateStore(paths.data_dir() / STATE_NAME).load()
    scheduler = Scheduler(registry, store, settings.tz)

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
        _log.info("MangaList %s headless runner; data folder %s; time zone %s; downloads %s",
                  __version__, paths.data_dir(), getattr(settings.tz, "key", settings.tz),
                  "on" if settings.downloads_enabled else "off (opt-in)")

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
