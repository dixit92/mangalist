"""Starts and watches MangaList's extension host in the container (``python -m mangalist.services.extensions.supervisor``;
the s6 service ``extension-host``). No Qt.

The host runs while the owner has chosen "Built-in extensions" for chapter downloads (Settings > Connected services;
:meth:`mangalist.store.downloads.SuwayomiSettings.backend`) - the supervisor reads that choice every
:data:`POLL_SECONDS`, so switching needs no container restart - or always with ``MANGALIST_EXTENSION_HOST=1``
(``0``: the service is off; ``auto``, the default: the setting decides). Its options come from MangaList's settings,
else the environment:

- ``--data <MangaList's data folder>/extension-host`` (in the container ``/config/extension-host``: the installed
  extensions, the manga / chapter id database, the download queue, the WebView's Chromium bundle when used) and
  ``--downloads <that>/downloads`` (the folder MangaList files finished chapters from - stored as the host's
  ``downloads_path`` at start, so filing works before any Test connection);
- ``--bind 127.0.0.1 --port <the port of the host's address in Settings>`` (4570): the host has no login, it stays on
  the container's loopback;
- ``--flaresolverr``: the FlareSolverr address in Settings, else ``MANGALIST_FLARESOLVERR_URL``;
- ``--repo`` (once per repository): the repository list as the owner last left it in Settings (Built-in extensions),
  else ``MANGALIST_EXTENSION_REPO`` (none: the host's own list - keiyoushi's on its first start). The host keeps its
  list in its data folder and takes an ``--repo`` it does not list yet; adding or removing a repository in Settings goes
  to the running host at once (its API), so a change of the list alone does not restart the host;
- ``--webview on|off``: the WebView switch in Settings (on by default; ``MANGALIST_EXTENSION_WEBVIEW=0`` turns it off).

A changed option restarts the host; a host that exits is started again (after :data:`BACKOFF_SECONDS` when it keeps
failing). SIGTERM stops the host, then the supervisor. The JVM's memory: ``MANGALIST_EXTENSION_HOST_OPTS`` (read by the
host's own start script; the image sets a cap).
"""

from __future__ import annotations

import logging
import os
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Mapping, Optional, Sequence
from urllib.parse import urlsplit

_log = logging.getLogger("mangalist.extension_host")

ENV_SWITCH = "MANGALIST_EXTENSION_HOST"             # auto (default) | 1 | 0
ENV_BIN = "MANGALIST_EXTENSION_HOST_BIN"
ENV_WEBVIEW = "MANGALIST_EXTENSION_WEBVIEW"
DEFAULT_BIN = "/opt/mangalist/extension-host/bin/mangalist-extension-host"
DATA_NAME = "extension-host"
POLL_SECONDS = 15.0
BACKOFF_SECONDS = 60.0
FAST_EXIT_SECONDS = 30.0                            # a host that exits sooner than this "keeps failing"
STOP_GRACE_SECONDS = 8.0                           # inside the container's 10 s SERVICES_GRACETIME
_FALSE = {"0", "false", "no", "off", "n"}
_TRUE = {"1", "true", "yes", "on", "y"}


@dataclass(frozen=True)
class Wanted:
    """Whether the host should run now, and with what command line."""

    run: bool
    command: tuple = ()
    downloads: str = ""
    why: str = ""


def switch(env: Optional[Mapping[str, str]] = None) -> str:
    value = (os.environ if env is None else env).get(ENV_SWITCH, "auto").strip().lower()
    return "on" if value in _TRUE else "off" if value in _FALSE else "auto"


def host_command(config, data_dir: Path, *, env: Optional[Mapping[str, str]] = None,
                 binary: Optional[str] = None) -> tuple:
    """The host's command line for *config* (:class:`mangalist.store.downloads.ExtensionHostConfig`)."""
    from ...downloads.extensions_backend import flaresolverr_url, repo_urls

    env = os.environ if env is None else env
    host_dir = Path(data_dir) / DATA_NAME
    port = urlsplit(config.url).port or 4570
    webview = config.webview and env.get(ENV_WEBVIEW, "1").strip().lower() not in _FALSE
    cmd: List[str] = [binary or env.get(ENV_BIN, DEFAULT_BIN), "--data", str(host_dir), "--bind", "127.0.0.1",
                      "--port", str(port), "--downloads", str(host_dir / "downloads"),
                      "--webview", "on" if webview else "off"]
    flare = flaresolverr_url(config, env)
    if flare:
        cmd += ["--flaresolverr", flare]
    for repo in repo_urls(config, env):
        cmd += ["--repo", repo]
    return tuple(cmd)


def without_repos(command: Sequence[str]) -> tuple:
    """*command* without its ``--repo`` options: what a restart is decided on (the running host already has a
    repository added or removed in Settings - through its API)."""
    out: List[str] = []
    skip = False
    for part in command:
        if skip:
            skip = False
        elif part == "--repo":
            skip = True
        else:
            out.append(part)
    return tuple(out)


def wanted(data_dir: Path, *, env: Optional[Mapping[str, str]] = None, open_settings: Optional[Callable] = None,
           binary: Optional[str] = None) -> Wanted:
    """Read the switch and MangaList's settings: should the host run, and how."""
    from ...store.downloads import ExtensionHostConfig

    mode = switch(env)
    if mode == "off":
        return Wanted(False, why=f"{ENV_SWITCH}=0")
    config, chosen = ExtensionHostConfig(), False
    settings = open_settings() if open_settings is not None else _open_settings(data_dir)
    if settings is not None:
        config, chosen = settings.extension_host(), settings.builtin
    if mode == "auto" and not chosen:
        return Wanted(False, why="chapter downloads go through Suwayomi")
    cmd = host_command(config, data_dir, env=env, binary=binary)
    return Wanted(True, cmd, downloads=cmd[cmd.index("--downloads") + 1],
                  why="always on" if mode == "on" else "Built-in extensions chosen")


class _SettingsOnly:
    """The two settings the supervisor reads (and the one it writes) straight from MangaList's database, without the
    :class:`~mangalist.store.Store` - which would migrate the schema, racing the window and the headless runner that
    create it at the container's first start (seen 2026-10-22: "duplicate column name"). Reads are read-only; the
    database is never created or migrated here."""

    def __init__(self, path: Path):
        self.path = Path(path)

    def _connect(self, readonly: bool = True):
        import sqlite3

        uri = f"file:{self.path}?mode=ro" if readonly else f"file:{self.path}?mode=rw"
        return sqlite3.connect(uri, uri=True, timeout=10)

    def get_setting(self, key: str, default=None):
        import json

        con = self._connect()
        try:
            row = con.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        finally:
            con.close()
        if row is None:
            return default
        try:
            return json.loads(row[0])
        except ValueError:
            return default

    def set_setting(self, key: str, value) -> None:
        import json

        con = self._connect(readonly=False)
        try:
            with con:
                con.execute("INSERT INTO settings (key, value) VALUES (?, ?) "
                            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                            (key, json.dumps(value, ensure_ascii=False)))
        finally:
            con.close()


def _open_settings(data_dir: Path):
    """MangaList's settings, or None while its database does not exist yet or has no settings table (it is never
    created or migrated here: see :class:`_SettingsOnly`)."""
    from ... import paths
    from ...store.downloads import SuwayomiSettings

    db = paths.db_file()
    if not db.is_file():
        return None
    settings = SuwayomiSettings(_SettingsOnly(db))
    try:
        settings.backend()
    except Exception:  # noqa: BLE001 - the window or the runner is still creating it: next poll
        _log.info("Extension host: MangaList's settings could not be read yet (%s)", db)
        return None
    return settings


class Supervisor:
    """One host process, kept as :func:`wanted` says. *spawn* / *clock* / *settings* are for tests."""

    def __init__(self, data_dir: Path, *, spawn: Callable[..., subprocess.Popen] = subprocess.Popen,
                 clock: Callable[[], float] = time.monotonic, env: Optional[Mapping[str, str]] = None,
                 open_settings: Optional[Callable] = None, binary: Optional[str] = None):
        self.data_dir = Path(data_dir)
        self._spawn = spawn
        self._clock = clock
        self._env = env
        self._open_settings = open_settings
        self._binary = binary
        self.process: Optional[subprocess.Popen] = None
        self.command: tuple = ()
        self.started_at = 0.0
        self.not_before = 0.0
        self.failures = 0
        self.last_why = ""

    def tick(self) -> None:
        """Start, restart or stop the host as needed (called every poll)."""
        want = wanted(self.data_dir, env=self._env, open_settings=self._open_settings, binary=self._binary)
        if want.why != self.last_why:
            _log.info("Extension host: %s", want.why)
            self.last_why = want.why
        running = self.process is not None and self.process.poll() is None
        if self.process is not None and not running:
            code = self.process.returncode
            quick = self._clock() - self.started_at < FAST_EXIT_SECONDS
            self.failures = self.failures + 1 if quick else 0
            if self.failures >= 2:
                self.not_before = self._clock() + BACKOFF_SECONDS
            _log.warning("Extension host: exited with status %s%s", code,
                         f"; starting it again in {BACKOFF_SECONDS:.0f} s" if self.failures >= 2 else "")
            self.process = None
        if not want.run:
            if self.process is not None:
                self.stop()
            return
        if self.process is not None and want.command != self.command:
            if without_repos(want.command) == without_repos(self.command):
                self.command = want.command             # the repository list: the host has it already
            else:
                _log.info("Extension host: its options changed; restarting it")
                self.stop()
        if self.process is None and self._clock() >= self.not_before:
            self._start(want)

    def _start(self, want: Wanted) -> None:
        Path(want.downloads).mkdir(parents=True, exist_ok=True)
        _log.info("Extension host: starting %s", " ".join(want.command))
        try:
            self.process = self._spawn(list(want.command))
        except OSError as exc:
            _log.error("Extension host: could not start %s (%s)", want.command[0], exc)
            self.failures += 1
            self.not_before = self._clock() + BACKOFF_SECONDS
            return
        self.command = want.command
        self.started_at = self._clock()
        settings = self._open_settings() if self._open_settings is not None else _open_settings(self.data_dir)
        if settings is not None:
            try:
                settings.learn_downloads_path(want.downloads)
            except Exception:  # noqa: BLE001 - learned again at the next Test connection
                _log.info("Extension host: the downloads folder could not be stored", exc_info=True)

    def stop(self) -> None:
        process, self.process = self.process, None
        if process is None or process.poll() is not None:
            return
        _log.info("Extension host: stopping it")
        process.terminate()
        try:
            process.wait(STOP_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(5)


def main(argv: Optional[Sequence[str]] = None) -> int:
    from ... import paths

    logging.basicConfig(level=logging.INFO, stream=sys.stdout, format="%(asctime)s %(levelname)s %(message)s")
    if switch() == "off":
        _log.info("Extension host: off (%s=0)", ENV_SWITCH)
        return 0
    sup = Supervisor(paths.data_dir())
    stopping = threading.Event()

    def _stop(_sig, _frame) -> None:
        stopping.set()

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    try:
        while not stopping.is_set():
            try:
                sup.tick()
            except Exception:  # noqa: BLE001 - a bad poll must not end the service
                _log.exception("Extension host: the supervisor's check failed")
            stopping.wait(POLL_SECONDS)
    finally:
        sup.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
