"""Jobs the headless runner can schedule, and the registry that names them.

- ``rescan``: re-scans every configured library root with the existing scanner (read-only; the
  owner and other tools keep adding files, so whatever is on disk is simply the new truth).
- ``dispatch-batch``: the batched download dispatch. Downloads are phase 3+ and opt-in: the job is
  registered but DISABLED unless downloads are turned on, and even then it only logs for now.
  Nothing is ever dispatched on discovery - only in these scheduled batches.

Roots come from a :class:`RootsProvider`. The default, :class:`StoreRootsProvider`, reads the
roots database (each root with its exclusions, which the rescan never scans) plus an optional
``MANGALIST_ROOTS`` list; :class:`ConfigRootsProvider` (``MANGALIST_ROOTS`` plus the GUI's last
root) remains for callers without a database.
"""

from __future__ import annotations

import logging
import os
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Protocol, runtime_checkable

from .schedule import Schedule

_log = logging.getLogger(__name__)

ENV_ROOTS = "MANGALIST_ROOTS"


class Cancelled(Exception):
    """Raised inside a job when the runner is shutting down."""


@dataclass
class JobResult:
    status: str = "ok"          # ok | error | skipped | cancelled
    message: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)


class JobContext:
    """What a running job may ask of the runner: whether to stop."""

    def __init__(self, should_stop: Callable[[], bool] = lambda: False):
        self._should_stop = should_stop

    @property
    def stop_requested(self) -> bool:
        return self._should_stop()

    def check(self) -> None:
        """Raise :class:`Cancelled` if a shutdown was requested (call between units of work)."""
        if self._should_stop():
            raise Cancelled()


JobFunc = Callable[[JobContext], JobResult]


@dataclass
class Job:
    name: str
    func: JobFunc
    schedule: Optional[Schedule]
    enabled: bool = True
    description: str = ""
    catch_up: bool = True       # run once after downtime if a due time was missed

    @property
    def active(self) -> bool:
        return self.enabled and self.schedule is not None


class JobRegistry:
    def __init__(self, jobs: Iterable[Job] = ()):
        self._jobs: Dict[str, Job] = {}
        for job in jobs:
            self.add(job)

    def add(self, job: Job) -> Job:
        if job.name in self._jobs:
            raise ValueError(f"duplicate job {job.name!r}")
        self._jobs[job.name] = job
        return job

    def get(self, name: str) -> Job:
        try:
            return self._jobs[name]
        except KeyError:
            raise KeyError(f"unknown job {name!r}; known: {', '.join(self.names())}") from None

    def names(self) -> List[str]:
        return list(self._jobs)

    def all(self) -> List[Job]:
        return list(self._jobs.values())

    def active(self) -> List[Job]:
        return [j for j in self._jobs.values() if j.active]

    def __contains__(self, name: object) -> bool:
        return name in self._jobs


# --- roots --------------------------------------------------------------------------------------

@runtime_checkable
class RootsProvider(Protocol):
    """Where the library roots come from: plain paths, or ``store.Root``-like objects (``path`` plus
    ``exclusions``, ``name``), whose exclusions the rescan applies."""

    def roots(self) -> List[Any]:
        ...


def _root_path(root: Any) -> Path:
    return Path(getattr(root, "path", root))


def _env_roots(env: Dict[str, str]) -> List[Path]:
    return [Path(part.strip()).expanduser()
            for part in (env.get(ENV_ROOTS) or "").split(os.pathsep) if part.strip()]


def _unique(roots: Iterable[Any]) -> List[Any]:
    unique: List[Any] = []
    seen = set()
    for root in roots:
        key = os.path.normcase(os.path.normpath(str(_root_path(root))))
        if key not in seen:
            seen.add(key)
            unique.append(root)
    return unique


class StoreRootsProvider:
    """The roots database (with each root's exclusions) plus ``MANGALIST_ROOTS``.

    Read on every call, so a root added in the GUI's Roots manager is used by the next scheduled
    rescan. A database root wins over the same path given in the environment (it carries the
    exclusions).
    """

    def __init__(self, env: Optional[Dict[str, str]] = None,
                 load_roots: Optional[Callable[[], List[Any]]] = None):
        self._env = env
        self._load_roots = load_roots

    def roots(self) -> List[Any]:
        env = os.environ if self._env is None else self._env
        loader = self._load_roots
        if loader is None:
            from ..store import get_store

            loader = lambda: get_store().list_roots()  # noqa: E731
        try:
            stored = list(loader())
        except Exception:  # noqa: BLE001 - a broken database must not stop the runner
            _log.warning("Could not read the library roots from the database", exc_info=True)
            stored = []
        return _unique([*stored, *_env_roots(env)])


class ConfigRootsProvider:
    """Today's roots: ``MANGALIST_ROOTS`` (``os.pathsep``-separated) plus the GUI's last root.

    Read on every call, so a root picked in the GUI is used by the next scheduled rescan.
    """

    def __init__(self, env: Optional[Dict[str, str]] = None,
                 load_config: Optional[Callable[[], Dict[str, Any]]] = None):
        self._env = env
        self._load_config = load_config

    def roots(self) -> List[Path]:
        env = os.environ if self._env is None else self._env
        found: List[Path] = _env_roots(env)
        loader = self._load_config
        if loader is None:
            from .. import config  # imported lazily: config is owned by another lane

            loader = config.load
        try:
            last = str(loader().get("last_root") or "").strip()
        except Exception:  # noqa: BLE001 - a broken config must not stop the runner
            _log.warning("Could not read the settings for the last opened root", exc_info=True)
            last = ""
        if last:
            found.append(Path(last).expanduser())
        return _unique(found)


# --- the jobs -----------------------------------------------------------------------------------

def make_rescan(provider: RootsProvider,
                scan: Optional[Callable[..., list]] = None) -> JobFunc:
    """A rescan over ``provider.roots()``; ``scan`` defaults to :func:`mangalist.scanner.scan_root`."""

    def rescan(ctx: JobContext) -> JobResult:
        scan_root = scan
        if scan_root is None:
            from ..scanner import scan_root  # noqa: F811 - lazy import keeps startup light
        roots = provider.roots()
        if not roots:
            return JobResult("skipped", "no library roots configured")
        per_root: List[Dict[str, Any]] = []
        failed = 0

        def progress(done: int, total: int, name: str) -> None:  # noqa: ARG001
            ctx.check()

        for spec in roots:
            ctx.check()
            started = time.monotonic()
            root = _root_path(spec)
            if not root.is_dir():
                _log.warning("Rescan: root is not a folder (not mounted?): %s", root)
                per_root.append({"root": str(root), "error": "not a folder"})
                failed += 1
                continue
            # A database root carries exclusions: those paths are never scanned (A5).
            extra = {} if isinstance(spec, (str, os.PathLike)) else {
                "exclusions": list(getattr(spec, "exclusions", None) or [])}
            try:
                entries = scan_root(root, progress=progress, **extra)
            except Cancelled:
                raise
            except Exception as exc:  # noqa: BLE001 - one bad root must not stop the others
                _log.exception("Rescan of %s failed", root)
                per_root.append({"root": str(root), "error": str(exc) or type(exc).__name__})
                failed += 1
                continue
            verdicts = Counter(getattr(getattr(e, "verdict", None), "name", "UNKNOWN")
                               for e in entries)
            files = sum(len(getattr(e, "files", ()) or ()) for e in entries)
            seconds = round(time.monotonic() - started, 2)
            _log.info("Rescan: %s - %d series, %d archives in %.1f s", root, len(entries), files,
                      seconds)
            per_root.append({"root": str(root), "series": len(entries), "archives": files,
                             "verdicts": dict(sorted(verdicts.items())), "seconds": seconds})
        ok = len(roots) - failed
        status = "ok" if failed == 0 else "error"
        return JobResult(status, f"{ok} of {len(roots)} roots scanned", {"roots": per_root})

    return rescan


def make_dispatch(downloads_enabled: bool) -> JobFunc:
    def dispatch_batch(ctx: JobContext) -> JobResult:  # noqa: ARG001
        if not downloads_enabled:
            _log.info("Dispatch batch: downloads are off (opt-in); nothing dispatched")
            return JobResult("skipped", "downloads are off")
        # Phase 3+: collect the missing items queued since the last batch and hand them to the
        # download clients. Until then the batch only records that it ran.
        _log.info("Dispatch batch: downloads are on, but dispatch is not implemented yet "
                  "(phase 3); nothing dispatched")
        return JobResult("skipped", "dispatch not implemented yet")

    return dispatch_batch


def build_registry(settings: "Any", provider: Optional[RootsProvider] = None) -> JobRegistry:
    """The runner's jobs from :class:`~mangalist.headless.settings.HeadlessSettings`."""
    provider = provider or StoreRootsProvider()
    return JobRegistry([
        Job("rescan", make_rescan(provider), settings.rescan_schedule,
            enabled=settings.rescan_schedule is not None,
            description="Rescan the library roots", catch_up=settings.catch_up),
        Job("dispatch-batch", make_dispatch(settings.downloads_enabled),
            settings.dispatch_schedule, enabled=settings.downloads_enabled,
            description="Dispatch the batched downloads (opt-in, off by default)",
            catch_up=settings.catch_up),
    ])
