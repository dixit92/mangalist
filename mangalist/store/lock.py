"""``.mangalist.lock``: one file per root so that only ONE MangaList instance writes a library at a time.

What it is and is not:

- It is only between MangaList instances (e.g. the Unraid container and a Windows desktop app that
  sees the same share over SMB). Other tools (FMD2, FreeFileSync, the owner) adding or changing files
  in the library is normal and is never blocked or treated as an error.
- Scans and reads never need it; only writes (journal plans: renames / moves) take it.
- It is a plain file, not an OS lock (``fcntl`` / ``LockFileEx`` do not reach across SMB from Linux
  and Windows reliably). Creation is exclusive (``O_CREAT | O_EXCL``, which SMB maps to "create new").

The file holds JSON: ``app``, ``token`` (this lock object), ``host``, ``pid``, ``acquired_at``,
``heartbeat`` (epoch seconds). The holder rewrites it every ``heartbeat_every`` seconds (atomically:
temp file + replace). Another instance may take it over when it is stale:

- same host and the holder's process is gone, or
- its heartbeat is older than ``stale_after`` seconds (default 5 minutes, ten missed heartbeats - generous
  because two machines' clocks can disagree), or
- the file cannot be read and has not changed for ``stale_after`` seconds.

Takeover renames the stale file to a unique name, checks it is still the one judged stale (else puts it
back and gives up), removes it, and creates a new lock exclusively; two instances racing for one stale
lock cannot both win.
"""

from __future__ import annotations

import json
import logging
import os
import socket
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

_log = logging.getLogger(__name__)

LOCK_NAME = ".mangalist.lock"
HEARTBEAT_SECONDS = 30.0
STALE_SECONDS = 300.0


class LockError(Exception):
    pass


class LockBusy(LockError):
    """Another MangaList instance holds the root."""

    def __init__(self, path: Path, holder: Optional["LockInfo"]):
        self.path, self.holder = path, holder
        who = f"{holder.host} (pid {holder.pid})" if holder else "another MangaList instance"
        super().__init__(f"{path.parent} is being changed by {who}")


class LockLost(LockError):
    """Our lock file was taken over or removed while we held it."""


@dataclass(frozen=True)
class LockInfo:
    token: str
    host: str
    pid: int
    acquired_at: float
    heartbeat: float
    app: str = "MangaList"

    @classmethod
    def parse(cls, text: str) -> Optional["LockInfo"]:
        try:
            d = json.loads(text)
            return cls(token=str(d["token"]), host=str(d["host"]), pid=int(d["pid"]),
                       acquired_at=float(d["acquired_at"]), heartbeat=float(d["heartbeat"]),
                       app=str(d.get("app", "MangaList")))
        except (TypeError, ValueError, KeyError):
            return None

    def dump(self) -> str:
        return json.dumps({"app": self.app, "token": self.token, "host": self.host, "pid": self.pid,
                           "acquired_at": self.acquired_at, "heartbeat": self.heartbeat}, indent=1)


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":  # pragma: no cover - Windows only
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return ctypes.GetLastError() == 5  # access denied: it exists
        try:
            code = ctypes.c_ulong()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return True
            return code.value == 259  # STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def this_host() -> str:
    return socket.gethostname() or "unknown-host"


class RootLock:
    """The lock of one root. Use as a context manager (acquire + heartbeat thread + release)::

        with RootLock(root_folder):
            ...  # write
    """

    def __init__(self, root, *, stale_after: float = STALE_SECONDS, heartbeat_every: float = HEARTBEAT_SECONDS,
                 clock: Callable[[], float] = time.time, host: Optional[str] = None, pid: Optional[int] = None):
        self.root = Path(root)
        self.path = self.root / LOCK_NAME
        self.stale_after = float(stale_after)
        self.heartbeat_every = float(heartbeat_every)
        self._clock = clock
        self.host = host or this_host()
        self.pid = os.getpid() if pid is None else int(pid)
        self.token = uuid.uuid4().hex
        self._info: Optional[LockInfo] = None
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.lost = False

    # --- reading -----------------------------------------------------------------------------------

    def read(self) -> Optional[LockInfo]:
        """The current holder, or None when the root is free (or the file is unreadable). A file that is in use for a
        moment (Windows: the holder's heartbeat is replacing it) is read again, not taken for "free"."""
        try:
            return LockInfo.parse(_in_use_retry(lambda: self.path.read_text(encoding="utf-8")))
        except FileNotFoundError:
            return None
        except OSError:
            return None

    def is_stale(self, info: Optional[LockInfo]) -> bool:
        now = self._clock()
        if info is None:
            # Unreadable (or being written): stale only when it has not changed for a while.
            try:
                return now - self.path.stat().st_mtime > self.stale_after
            except OSError:
                return False
        if info.host.lower() == self.host.lower() and info.pid != self.pid and not pid_alive(info.pid):
            return True
        return now - info.heartbeat > self.stale_after

    @property
    def held(self) -> bool:
        return self._info is not None and not self.lost

    # --- acquire / refresh / release ---------------------------------------------------------------

    def acquire(self, force: bool = False) -> "RootLock":
        """Take the lock or raise :class:`LockBusy`. *force* takes over a live lock (the user said so)."""
        if self.held:
            return self
        for _attempt in range(3):
            try:
                self._create()
                return self
            except FileExistsError:
                pass
            except PermissionError as exc:
                raise LockError(f"cannot write {self.path} (read-only root?): {exc}") from exc
            current = self.read()
            if current is not None and current.token == self.token:
                self._info = current
                return self
            if not (force or self.is_stale(current)):
                raise LockBusy(self.path, current)
            self._take_over(current)
        raise LockBusy(self.path, self.read())

    def _create(self) -> None:
        now = self._clock()
        info = LockInfo(token=self.token, host=self.host, pid=self.pid, acquired_at=now, heartbeat=now)
        fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        try:
            os.write(fd, info.dump().encode("utf-8"))
            os.fsync(fd)
        finally:
            os.close(fd)
        self._info = info
        self.lost = False
        _log.info("Root lock taken: %s", self.path)

    def _take_over(self, judged: Optional[LockInfo]) -> None:
        aside = self.path.with_name(f"{LOCK_NAME}.stale-{self.token}")
        try:
            os.rename(self.path, aside)
        except FileNotFoundError:
            return  # released or taken meanwhile: try again
        except OSError as exc:
            raise LockError(f"cannot take over {self.path}: {exc}") from exc
        try:
            moved = LockInfo.parse(aside.read_text(encoding="utf-8"))
        except OSError:
            moved = None
        if moved != judged:
            # Not the lock we judged stale (refreshed or replaced in between): put it back, give up.
            try:
                if not os.path.lexists(self.path):
                    os.rename(aside, self.path)
            except OSError:
                _log.warning("Could not restore the lock file %s", self.path, exc_info=True)
            raise LockBusy(self.path, moved)
        _log.warning("Taking over the stale root lock of %s (pid %s) at %s",
                     judged.host if judged else "?", judged.pid if judged else "?", self.path)
        try:
            os.remove(aside)  # our own lock artefact, never library content
        except OSError:
            _log.warning("Could not remove %s", aside, exc_info=True)

    def refresh(self) -> None:
        """Rewrite the heartbeat; raises :class:`LockLost` when the file is no longer ours."""
        if self._info is None:
            raise LockLost(f"{self.path} is not held")
        current = self.read()
        if current is None or current.token != self.token:
            self.lost = True
            raise LockLost(f"{self.path} was taken over or removed")
        info = LockInfo(token=self.token, host=self.host, pid=self.pid, acquired_at=self._info.acquired_at,
                        heartbeat=self._clock())
        tmp = self.path.with_name(f"{LOCK_NAME}.{self.token}.tmp")
        tmp.write_text(info.dump(), encoding="utf-8")
        _in_use_retry(lambda: os.replace(tmp, self.path))     # Windows: a reader may have the file open this instant
        self._info = info

    def check(self) -> None:
        """Raise :class:`LockLost` unless we still hold the lock (read from disk)."""
        current = self.read()
        if self.lost or current is None or current.token != self.token:
            self.lost = True
            raise LockLost(f"{self.path} is no longer held by this instance")

    def release(self) -> None:
        self._stop_heartbeat()
        if self._info is None:
            return
        current = self.read()
        self._info = None
        if current is not None and current.token == self.token:
            try:
                os.remove(self.path)
            except FileNotFoundError:
                pass
            _log.info("Root lock released: %s", self.path)
        else:
            _log.warning("Root lock %s was no longer ours at release", self.path)

    # --- heartbeat ---------------------------------------------------------------------------------

    def start_heartbeat(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()

        def beat() -> None:
            while not self._stop.wait(self.heartbeat_every):
                try:
                    self.refresh()
                except LockLost:
                    _log.error("Lost the root lock %s", self.path)
                    return
                except OSError:
                    _log.warning("Heartbeat of %s failed", self.path, exc_info=True)

        self._thread = threading.Thread(target=beat, name=f"root-lock {self.root.name}", daemon=True)
        self._thread.start()

    def _stop_heartbeat(self) -> None:
        if self._thread is not None:
            self._stop.set()
            self._thread.join(timeout=5)
            self._thread = None

    def __enter__(self) -> "RootLock":
        self.acquire()
        self.start_heartbeat()
        return self

    def __exit__(self, *exc) -> None:
        self.release()


IN_USE_TRIES = 10
IN_USE_WAIT = 0.02          # seconds between tries: a reader or a replace holds the file for well under that


def _in_use_retry(fn):
    """*fn()*, tried again while the file is in use (Windows reports a sharing violation as PermissionError while
    another process opens or replaces it; POSIX never does). Other errors, and the last try's, are raised."""
    for attempt in range(IN_USE_TRIES):
        try:
            return fn()
        except PermissionError:
            if attempt == IN_USE_TRIES - 1:
                raise
            time.sleep(IN_USE_WAIT)
